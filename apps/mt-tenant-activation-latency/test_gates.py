"""
Run with: python3 -m unittest discover -s apps/mt-tenant-activation-latency

The distributions below are the real ones measured on a 3-node multi-tenant
cluster either side of the fix, including the port-forward reconnect that made a
max-based gate a coin toss. These tests pin that the gate separates the builds and
is not moved by that artifact.
"""

import unittest

from gates import (
    DeadlineAccumulator,
    count_deadline_waits,
    evaluate,
    percentile,
    summarise,
)

QUIET = [4.0] * 99
# before the fix: the stall is pervasive, so p99 itself is ~12s
ROLLING_BEFORE = [3.2] * 100 + [11_200.0] * 30 + [12_452.0] * 5
WRITES_BEFORE = [122.0] * 240 + [7_584.0] * 8
# after: p99 in the tens of ms, and one 15s outlier from the client's port-forward
# reconnecting mid-roll -- a healthy build that a max-based gate would have failed
ROLLING_AFTER = [3.8] * 200 + [12.8] * 4 + [15_015.0]
WRITES_AFTER = [6.4] * 640 + [159.0] * 9

LIMITS = dict(max_query_p99_ms=1000, max_write_p99_ms=500, min_queries=30, min_writes=30)


def metrics_text(start: float, deadline: int = 0, immediate: int = 100) -> str:
    """One pod's metrics, as the deadline accounting reads them."""
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

    def test_a_handful_of_fast_writes_cannot_clear_the_write_p99(self):
        """A write worker that barely ran leaves a thin distribution, not a pass."""
        passed, lines = evaluate(QUIET, ROLLING_AFTER, [1.0] * 4, 0, **LIMITS)
        self.assertFalse(passed)
        self.assertTrue(any("too thin a distribution" in l for l in lines))


class TestDeadlineWaitsAcrossRestarts(unittest.TestCase):
    """
    These counters reset when a pod restarts, which is exactly what the window
    under test does, so the delta has to be taken per pod with the restart
    detected rather than guessed from the numbers going backwards.
    """

    def test_healthy_cluster_reports_zero_not_unknown(self):
        t = metrics_text(100.0)
        self.assertEqual(count_deadline_waits({"w": t}, {"w": t}), 0)

    def test_delta_when_the_pod_did_not_restart(self):
        got = count_deadline_waits(
            {"w": metrics_text(100.0, deadline=5)}, {"w": metrics_text(100.0, deadline=12)}
        )
        self.assertEqual(got, 7)

    def test_a_restarted_pod_counts_from_zero(self):
        """Its counters reset, so the new value IS the window, not a negative delta."""
        got = count_deadline_waits(
            {"w": metrics_text(100.0, deadline=5000)}, {"w": metrics_text(900.0, deadline=3)}
        )
        self.assertEqual(got, 3)

    def test_a_pod_absent_before_counts_in_full(self):
        got = count_deadline_waits({}, {"w": metrics_text(900.0, deadline=4)})
        self.assertEqual(got, 4)

    def test_summed_across_pods(self):
        before = {"a": metrics_text(1.0), "b": metrics_text(2.0)}
        after = {"a": metrics_text(1.0, deadline=2), "b": metrics_text(2.0, deadline=3)}
        self.assertEqual(count_deadline_waits(before, after), 5)

    def test_a_build_without_the_metric_is_unknown(self):
        self.assertIsNone(count_deadline_waits({}, {"w": "go_goroutines 42\n"}))
        self.assertIsNone(count_deadline_waits({}, {}))


class TestDeadlinesBeforeARestart(unittest.TestCase):
    """
    The waits worth catching are the ones a pod serves while an *earlier* pod is
    down, so they are on its counter before it restarts in its turn. Two scrapes
    across a rolling restart cannot see them: the reset means the final value is
    all that is left, and a counter that climbed to 7 and then reset reports as
    zero. Sampling the window keeps them.
    """

    def test_two_scrapes_lose_them(self):
        before = {"w": metrics_text(100.0)}
        after = {"w": metrics_text(900.0)}
        self.assertEqual(count_deadline_waits(before, after), 0)

    def test_sampling_keeps_them(self):
        acc = DeadlineAccumulator({"w": metrics_text(100.0)})
        acc.observe({"w": metrics_text(100.0, deadline=7)})
        acc.observe({"w": metrics_text(900.0)})  # restarted, counters back to zero
        self.assertEqual(acc.total(), 7)

    def test_a_pod_missing_from_a_sample_resumes_on_the_next(self):
        """Mid-roll a pod cannot be scraped; it must not restart the accounting."""
        acc = DeadlineAccumulator({"a": metrics_text(1.0), "b": metrics_text(2.0)})
        acc.observe({"a": metrics_text(1.0, deadline=3)})
        acc.observe({"a": metrics_text(1.0, deadline=3), "b": metrics_text(2.0, deadline=4)})
        self.assertEqual(acc.total(), 7)

    def test_deadlines_on_both_sides_of_one_restart_are_summed(self):
        acc = DeadlineAccumulator({"w": metrics_text(100.0)})
        acc.observe({"w": metrics_text(100.0, deadline=2)})
        acc.observe({"w": metrics_text(900.0, deadline=5)})
        self.assertEqual(acc.total(), 7)

    def test_a_build_without_the_metric_stays_unknown_while_sampling(self):
        acc = DeadlineAccumulator({"w": "go_goroutines 42\n"})
        acc.observe({"w": "go_goroutines 43\n"})
        self.assertIsNone(acc.total())


class TestFailedCallsDoNotPassAsLatency(unittest.TestCase):
    """
    A refused call is not a request the cluster served. Counting refusals as
    samples lets an instant connection refusal fill min_queries and hold p99
    down, so they are counted separately and gated on their share.
    """

    def test_a_window_of_refusals_cannot_satisfy_min_queries(self):
        passed, lines = evaluate(QUIET, [1.0] * 3, WRITES_AFTER, 0, **LIMITS, query_failures=500)
        self.assertFalse(passed)
        self.assertTrue(any("nothing was measured where it matters" in l for l in lines))

    def test_mostly_failing_queries_fail_the_run(self):
        passed, lines = evaluate(
            QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS, query_failures=400
        )
        self.assertFalse(passed)
        self.assertTrue(any("query calls failed" in l and "FAILED" in l for l in lines))

    def test_mostly_failing_writes_fail_the_run(self):
        passed, lines = evaluate(
            QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS, write_failures=10_000
        )
        self.assertFalse(passed)
        self.assertTrue(any("write calls failed" in l and "FAILED" in l for l in lines))

    def test_a_handful_of_reconnect_refusals_only_gets_a_note(self):
        passed, lines = evaluate(QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS, query_failures=4)
        self.assertTrue(passed, lines)
        self.assertTrue(any("note:" in l and "query calls failed" in l for l in lines))

    def test_the_allowance_is_tunable(self):
        passed, _ = evaluate(
            QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS, query_failures=4, max_failure_ratio=0.0
        )
        self.assertFalse(passed)


class TestCollectionProblemsFailTheRun(unittest.TestCase):
    """
    A run whose numbers are otherwise spotless must still fail when something
    went wrong collecting them -- a rejected rollout, a worker that never
    finished, a scrape that could not be completed. Each of those makes the
    latency look good for the wrong reason.
    """

    def test_a_problem_fails_an_otherwise_clean_run(self):
        passed, lines = evaluate(
            QUIET,
            ROLLING_AFTER,
            WRITES_AFTER,
            0,
            **LIMITS,
            problems=["kubectl rollout restart exited 1: forbidden"],
        )
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: kubectl rollout restart exited 1" in l for l in lines))

    def test_every_problem_is_reported_not_just_the_first(self):
        passed, lines = evaluate(
            QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS, problems=["one", "two"]
        )
        self.assertFalse(passed)
        self.assertTrue(any(l == "FAILED: one" for l in lines))
        self.assertTrue(any(l == "FAILED: two" for l in lines))


if __name__ == "__main__":
    unittest.main()
