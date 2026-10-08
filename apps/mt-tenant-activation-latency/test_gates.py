"""
Run with: python3 -m unittest discover -s apps/mt-tenant-activation-latency

The distributions below are the real ones measured on a 3-node multi-tenant
cluster either side of the fix, including the port-forward reconnect that made a
max-based gate a coin toss. These tests pin that the gate separates the builds and
is not moved by that artifact.
"""

import unittest

from gates import count_deadline_waits, evaluate, percentile, summarise

QUIET = [4.0] * 99
# before the fix: the stall is pervasive, so p99 itself is ~12s
ROLLING_BEFORE = [3.2] * 100 + [11_200.0] * 30 + [12_452.0] * 5
WRITES_BEFORE = [122.0] * 240 + [7_584.0] * 8
# after: p99 in the tens of ms, and one 15s outlier from the client's port-forward
# reconnecting mid-roll -- a healthy build that a max-based gate would have failed
ROLLING_AFTER = [3.8] * 200 + [12.8] * 4 + [15_015.0]
WRITES_AFTER = [6.4] * 640 + [159.0] * 9

LIMITS = dict(max_query_p99_ms=1000, max_write_p99_ms=500, min_queries=30)


class TestPercentile(unittest.TestCase):
    def test_edges(self):
        self.assertIsNone(percentile([], 0.99))
        self.assertEqual(percentile([7.0], 0.99), 7.0)
        self.assertEqual(percentile([1, 2, 3, 4, 5], 0.0), 1)
        self.assertEqual(percentile([1, 2, 3, 4, 5], 1.0), 5)

    def test_summary_of_empty_is_not_a_crash(self):
        s = summarise([])
        self.assertEqual(s["count"], 0)
        self.assertIsNone(s["max"])


class TestGate(unittest.TestCase):
    def test_before_the_fix_fails(self):
        passed, lines = evaluate(QUIET, ROLLING_BEFORE, WRITES_BEFORE, 3925, **LIMITS)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: query p99" in l for l in lines))
        self.assertTrue(any("FAILED: write p99" in l for l in lines))
        self.assertTrue(any("hit their deadline" in l for l in lines))

    def test_after_the_fix_passes_despite_one_reconnect_outlier(self):
        """The 15s sample is a port-forward reconnect; it must not fail the run."""
        passed, lines = evaluate(QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS)
        self.assertTrue(passed, lines)
        self.assertTrue(any("slowest single query was 15015ms" in l for l in lines))
        self.assertTrue(any("Not gated" in l for l in lines))

    def test_writes_alone_can_fail_a_run(self):
        """Writes are the broadest signal; a clean query p99 must not excuse them."""
        passed, lines = evaluate(QUIET, ROLLING_AFTER, WRITES_BEFORE, 0, **LIMITS)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: write p99" in l for l in lines))

    def test_the_quiet_window_never_gates(self):
        passed, lines = evaluate([60_000.0] * 99, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS)
        self.assertTrue(passed, "only the rolling window gates")
        self.assertTrue(any("queries, quiet" in l for l in lines))

    def test_a_build_without_the_metric_still_gates_on_latency(self):
        passed, _ = evaluate(QUIET, ROLLING_BEFORE, WRITES_BEFORE, None, **LIMITS)
        self.assertFalse(passed)
        passed, lines = evaluate(QUIET, ROLLING_AFTER, WRITES_AFTER, None, **LIMITS)
        self.assertTrue(passed)
        self.assertTrue(any("unavailable on this build" in l for l in lines))

    def test_a_rollout_that_measured_nothing_fails(self):
        passed, lines = evaluate(QUIET, [1.0] * 3, WRITES_AFTER, 0, **LIMITS)
        self.assertFalse(passed)
        self.assertTrue(any("nothing was measured where it matters" in l for l in lines))


class TestDeadlineWaitsAcrossRestarts(unittest.TestCase):
    """
    These counters reset when a pod restarts, which is exactly what the window
    under test does, so the delta has to be taken per pod with the restart
    detected rather than guessed from the numbers going backwards.
    """

    def _text(self, start: float, deadline: int = 0, immediate: int = 100) -> str:
        s = f"process_start_time_seconds {start}\n"
        s += (
            "weaviate_cluster_store_wait_for_index_duration_seconds_count"
            f'{{nodeID="w",outcome="immediate"}} {immediate}\n'
        )
        if deadline:
            s += (
                "weaviate_cluster_store_wait_for_index_duration_seconds_count"
                f'{{nodeID="w",outcome="deadline"}} {deadline}\n'
            )
        return s

    def test_healthy_cluster_reports_zero_not_unknown(self):
        t = self._text(100.0)
        self.assertEqual(count_deadline_waits({"w": t}, {"w": t}), 0)

    def test_delta_when_the_pod_did_not_restart(self):
        got = count_deadline_waits(
            {"w": self._text(100.0, deadline=5)}, {"w": self._text(100.0, deadline=12)}
        )
        self.assertEqual(got, 7)

    def test_a_restarted_pod_counts_from_zero(self):
        """Its counters reset, so the new value IS the window, not a negative delta."""
        got = count_deadline_waits(
            {"w": self._text(100.0, deadline=5000)}, {"w": self._text(900.0, deadline=3)}
        )
        self.assertEqual(got, 3)

    def test_a_pod_absent_before_counts_in_full(self):
        got = count_deadline_waits({}, {"w": self._text(900.0, deadline=4)})
        self.assertEqual(got, 4)

    def test_summed_across_pods(self):
        before = {"a": self._text(1.0), "b": self._text(2.0)}
        after = {"a": self._text(1.0, deadline=2), "b": self._text(2.0, deadline=3)}
        self.assertEqual(count_deadline_waits(before, after), 5)

    def test_a_build_without_the_metric_is_unknown(self):
        self.assertIsNone(count_deadline_waits({}, {"w": "go_goroutines 42\n"}))
        self.assertIsNone(count_deadline_waits({}, {}))


if __name__ == "__main__":
    unittest.main()
