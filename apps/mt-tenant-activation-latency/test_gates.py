"""
Run with: python3 -m unittest discover -s apps/mt-tenant-activation-latency

The distributions below are the real ones measured on a 3-node multi-tenant
cluster either side of the fix, so these tests pin that the gate separates the two
rather than passing both.
"""

import unittest

from gates import count_deadline_waits, evaluate, percentile, summarise

QUIET = [900.0] * 60
# before: the reconcile opened every local HOT shard inside the serial apply, so
# queries needing a recent schema version waited out the consistency timeout
ROLLING_BEFORE = [900.0] * 90 + [10_000.0] * 8 + [21_900.0, 50_200.0]
# after: the apply stays cheap and the waits resolve in tens of ms
ROLLING_AFTER = [900.0] * 90 + [1_100.0] * 9 + [1_350.0]
WRITES = [40.0] * 50


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
        passed, lines = evaluate(QUIET, ROLLING_BEFORE, WRITES, 3925, 5000, 30)
        self.assertFalse(passed)
        self.assertTrue(any("slowest query while rolling" in l and "FAILED" in l for l in lines))
        self.assertTrue(any("hit their deadline" in l for l in lines))

    def test_after_the_fix_passes(self):
        passed, lines = evaluate(QUIET, ROLLING_AFTER, WRITES, 0, 5000, 30)
        self.assertTrue(passed, lines)

    def test_the_quiet_window_is_reported_but_never_gates(self):
        """A build slow at rest must still be judged on the rollout."""
        passed, lines = evaluate([60_000.0] * 60, ROLLING_AFTER, WRITES, 0, 5000, 30)
        self.assertTrue(passed, "only the rolling window gates")
        self.assertTrue(any("queries, quiet" in l for l in lines))

    def test_a_build_without_the_metric_still_gates_on_latency(self):
        passed, _ = evaluate(QUIET, ROLLING_BEFORE, WRITES, None, 5000, 30)
        self.assertFalse(passed, "client latency alone must still catch it")
        passed, lines = evaluate(QUIET, ROLLING_AFTER, WRITES, None, 5000, 30)
        self.assertTrue(passed)
        self.assertTrue(any("unavailable on this build" in l for l in lines))

    def test_a_rollout_that_measured_nothing_fails(self):
        passed, lines = evaluate(QUIET, [1.0] * 3, WRITES, 0, 5000, 30)
        self.assertFalse(passed, "too few samples must not read as a pass")
        self.assertTrue(any("nothing was measured where it matters" in l for l in lines))


class TestServerSideIsReportedNotGated(unittest.TestCase):
    """
    Measured on both builds the server's own histogram read 262-360ms against
    263-432ms, so it cannot gate. It is still reported, because it is the signal
    watched when judging a rollout.
    """

    def test_a_bad_server_p99_alone_does_not_fail_the_run(self):
        passed, lines = evaluate(
            QUIET,
            ROLLING_AFTER,
            WRITES,
            0,
            5000,
            30,
            server={"queries": 900.0, "p50_ms": 40.0, "p99_ms": 30_000.0, "pods": 3.0},
        )
        self.assertTrue(passed, "server-side must not gate")
        self.assertTrue(any("reported, not gated" in l for l in lines))

    def test_missing_pods_are_called_out(self):
        _, lines = evaluate(
            QUIET,
            ROLLING_AFTER,
            WRITES,
            0,
            5000,
            30,
            server={
                "queries": 10.0,
                "p50_ms": 1.0,
                "p99_ms": 2.0,
                "pods": 2.0,
                "pods_missing": 1.0,
            },
        )
        self.assertTrue(any("could not be scraped" in l for l in lines))


if __name__ == "__main__":
    unittest.main()


class TestDeadlineWaitParsing(unittest.TestCase):
    """
    A healthy cluster never observes the deadline outcome, so that child series is
    absent from /metrics. Keying "does this build expose the metric" on it reported
    a clean post-fix run as unmeasurable, which is how a broken A/B looked like a
    pass on both arms.
    """

    IMMEDIATE_ONLY = (
        "weaviate_cluster_store_wait_for_index_duration_seconds_count"
        '{nodeID="weaviate-0",outcome="immediate"} 21344\n'
        "weaviate_cluster_store_wait_for_index_duration_seconds_sum"
        '{nodeID="weaviate-0",outcome="immediate"} 0.08\n'
    )
    WITH_DEADLINES = IMMEDIATE_ONLY + (
        "weaviate_cluster_store_wait_for_index_duration_seconds_count"
        '{nodeID="weaviate-0",outcome="deadline"} 137\n'
    )

    def test_healthy_cluster_reports_zero_not_unknown(self):
        self.assertEqual(count_deadline_waits({"weaviate-0": self.IMMEDIATE_ONLY}), 0)

    def test_deadlines_are_summed_across_pods(self):
        got = count_deadline_waits(
            {"weaviate-0": self.WITH_DEADLINES, "weaviate-1": self.WITH_DEADLINES}
        )
        self.assertEqual(got, 274)

    def test_a_build_without_the_metric_is_unknown(self):
        self.assertIsNone(count_deadline_waits({"weaviate-0": "go_goroutines 42\n"}))
        self.assertIsNone(count_deadline_waits({}))

    def test_zero_deadlines_reaches_the_passing_branch(self):
        _, lines = evaluate(
            QUIET, ROLLING_AFTER, WRITES, count_deadline_waits({"w": self.IMMEDIATE_ONLY}), 5000, 30
        )
        self.assertTrue(any("no schema-version wait hit its deadline" in l for l in lines))
        self.assertFalse(any("unavailable on this build" in l for l in lines))
