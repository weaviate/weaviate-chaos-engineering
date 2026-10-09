"""
Tests for the restart benchmark's pass/fail thresholds.

Run with: python3 -m unittest discover -s apps/restart-highly-available-qps-benchmark

Pure CSV analysis, so no cluster is needed. The numbers below are the ones real
runs produced, including the shape this benchmark used to pass: weaviate#13396
held queries for 8s while throughput stayed near target.
"""

import csv
import os
import tempfile
import unittest
from typing import List, Optional, Tuple

from validation import validate_benchmark_csv

HEADER = [
    "timestamp",
    "phase_name",
    "p50_latency",
    "p90_latency",
    "p95_latency",
    "p99_latency",
    "total_queries",
    "actual_qps",
]


def write_profile(
    path: str,
    pre: List[Tuple[float, float]],
    post: List[Tuple[float, float]],
    sentinel: Optional[str] = "rolling_restart_event",
) -> None:
    """Write a CSV from (p99_ms, qps) pairs either side of the restart sentinel."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        t, n = 1790122869.0, 0

        def row(p99: float, qps: float) -> None:
            nonlocal t, n
            n += int(qps)
            w.writerow(
                [
                    f"{t:.6f}",
                    "Main Test",
                    f"{p99 / 2:.2f}",
                    f"{p99 * 0.8:.2f}",
                    f"{p99 * 0.9:.2f}",
                    f"{p99:.2f}",
                    n,
                    f"{qps:.2f}",
                ]
            )
            t += 1.0

        for p99, qps in pre:
            row(p99, qps)
        if sentinel is not None:
            w.writerow([f"{t:.6f}", sentinel, "", "", "", "", 0, 0])
            t += 1.0
        for p99, qps in post:
            row(p99, qps)


def flat(n: int, p99: float, qps: float) -> List[Tuple[float, float]]:
    return [(p99, qps)] * n


class TestValidateBenchmarkCsv(unittest.TestCase):
    def check(self, pre, post, sentinel="rolling_restart_event", **kwargs):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "benchmark_results_x.csv")
            write_profile(path, pre, post, sentinel)
            return validate_benchmark_csv(path, target_qps=20.0, **kwargs)

    def test_profiles(self):
        cases = [
            {
                "name": "healthy rollout: brief dip, mostly fine",
                "pre": flat(20, 400, 19),
                # ~8% of a 300s window degraded, inside the 15% allowed
                "post": flat(25, 1400, 18) + flat(275, 420, 19),
                "want_pass": True,
            },
            {
                # The shape this benchmark passed: throughput holds, latency does not.
                "name": "latency bad for a third of the window (PR 13396)",
                "pre": flat(20, 455, 19),
                "post": flat(100, 8200, 16) + flat(200, 455, 19),
                "want_pass": False,
                "want_reason": "degraded for",
            },
            {
                "name": "throughput collapses",
                "pre": flat(20, 400, 19),
                "post": flat(5, 500, 1.0) + flat(295, 400, 19),
                "want_pass": False,
                "want_reason": "QPS sustained below threshold",
            },
            {
                "name": "a few slow seconds are not a regression",
                "pre": flat(20, 400, 19),
                "post": flat(6, 9000, 18) + flat(294, 400, 19),
                "want_pass": True,
            },
            {
                "name": "no sentinel means annotation broke",
                "pre": flat(20, 400, 19),
                "post": flat(200, 400, 19),
                "sentinel": None,
                "want_pass": False,
                "want_reason": "No restart sentinel",
            },
            {
                "name": "too little data to judge",
                "pre": flat(2, 400, 19),
                "post": flat(2, 400, 19),
                "want_pass": False,
                "want_reason": "Too few rows",
            },
        ]
        for c in cases:
            with self.subTest(c["name"]):
                passed, reason = self.check(
                    c["pre"], c["post"], c.get("sentinel", "rolling_restart_event")
                )
                self.assertEqual(c["want_pass"], passed, f"{c['name']}: {reason}")
                if c.get("want_reason"):
                    self.assertIn(c["want_reason"], reason)

    def test_short_window_is_not_judged_on_latency(self):
        """
        A row covers one second of queries, so its p99 is really a maximum. Over
        180 rows the degraded share is still too noisy to gate on -- the same
        build produced 32 timeouts in one run and 0 in the next.
        """
        passed, reason = self.check(flat(20, 400, 19), flat(100, 8200, 18))
        self.assertTrue(passed, reason)
        self.assertIn("not judged", reason)

    def test_saturated_run_is_not_judged_on_latency(self):
        """
        A runner holding 10.3 of 20 QPS at ~1000ms p99 before anything restarted
        cannot support a verdict about tail latency; widening the thresholds to
        cover it would spend that slack on every healthy run.
        """
        passed, reason = self.check(
            flat(20, 1000, 10.3), flat(200, 4081, 10) + flat(150, 1000, 10.3)
        )
        self.assertTrue(passed, reason)
        self.assertIn("saturated", reason)

    def test_throughput_only_check_misses_a_latency_regression(self):
        """
        Pins the gap this exists to close: the 13396 profile passes when only
        throughput is examined, which is why the benchmark went green against a
        build that held queries for 8s.
        """
        pre, post = flat(20, 455, 19), flat(100, 8200, 16) + flat(200, 455, 19)
        passed, _ = self.check(pre, post, max_degraded_fraction=1.0)
        self.assertTrue(passed, "throughput alone sees nothing wrong")
        passed, reason = self.check(pre, post)
        self.assertFalse(passed)
        self.assertIn("degraded for", reason)


if __name__ == "__main__":
    unittest.main()
