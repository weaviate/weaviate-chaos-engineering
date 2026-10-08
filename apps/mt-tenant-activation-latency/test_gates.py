"""
Run with: python3 -m unittest discover -s apps/mt-tenant-activation-latency

The numbers below are the real ones measured on a 3-node multi-tenant cluster
before and after the activation fix, so these tests pin that the gate actually
separates the two rather than passing both.
"""

import unittest

from gates import evaluate, percentile, summarise


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
    # before: activation opened shards inside the serial apply, so queries that
    # needed a recent schema version waited out the consistency timeout
    BEFORE = [900.0] * 90 + [10_000.0] * 8 + [21_900.0, 50_200.0]
    # after: the apply stays cheap and the waits resolve in tens of ms
    AFTER = [900.0] * 90 + [1_100.0] * 9 + [1_350.0]

    def test_before_the_fix_fails(self):
        passed, lines = evaluate(
            self.BEFORE, [250.0], deadline_waits=3925, max_query_ms=5000, min_queries=50
        )
        self.assertFalse(passed)
        self.assertTrue(any("slowest query" in l and "FAILED" in l for l in lines))
        self.assertTrue(any("hit their deadline" in l for l in lines))

    def test_after_the_fix_passes(self):
        passed, lines = evaluate(
            self.AFTER, [2.0], deadline_waits=0, max_query_ms=5000, min_queries=50
        )
        self.assertTrue(passed, lines)

    def test_a_build_without_the_metric_still_gates_on_latency(self):
        passed, _ = evaluate(
            self.BEFORE, [250.0], deadline_waits=None, max_query_ms=5000, min_queries=50
        )
        self.assertFalse(passed, "latency alone must still catch it")
        passed, lines = evaluate(
            self.AFTER, [2.0], deadline_waits=None, max_query_ms=5000, min_queries=50
        )
        self.assertTrue(passed)
        self.assertTrue(any("unavailable on this build" in l for l in lines))

    def test_a_run_that_measured_nothing_fails(self):
        passed, lines = evaluate(
            [1.0] * 3, [2.0], deadline_waits=0, max_query_ms=5000, min_queries=50
        )
        self.assertFalse(passed, "too few samples must not read as a pass")
        self.assertTrue(any("nothing was measured" in l for l in lines))


if __name__ == "__main__":
    unittest.main()


class TestServerSideIsReportedNotGated(unittest.TestCase):
    """
    Measured on both builds the server's own histogram read 262-360ms against
    263-432ms, so it cannot gate. It is still reported, because it is the signal
    the team watches when judging a rollout.
    """

    def test_a_bad_server_p99_alone_does_not_fail_the_run(self):
        passed, lines = evaluate(
            TestGate.AFTER,
            [2.0],
            deadline_waits=0,
            max_query_ms=5000,
            min_queries=50,
            server={"queries": 900.0, "p50_ms": 40.0, "p99_ms": 30_000.0, "pods": 3.0},
        )
        self.assertTrue(passed, "server-side must not gate")
        self.assertTrue(any("reported, not gated" in l for l in lines))

    def test_missing_pods_are_called_out(self):
        _, lines = evaluate(
            TestGate.AFTER,
            [2.0],
            deadline_waits=0,
            max_query_ms=5000,
            min_queries=50,
            server={
                "queries": 10.0,
                "p50_ms": 1.0,
                "p99_ms": 2.0,
                "pods": 2.0,
                "pods_missing": 1.0,
            },
        )
        self.assertTrue(any("could not be scraped" in l for l in lines))
