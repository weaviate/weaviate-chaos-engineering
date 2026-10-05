"""
Tests for reading query latency and error rate out of Weaviate's metrics.

Run with: python3 -m unittest discover -s apps/restart-highly-available-qps-benchmark

The metrics text below is the real format, bounds included: 10, 50, 100, 500,
1000, 5000, 10000, 60000, 300000, +Inf. Those brackets are what make this worth
reading -- a healthy rollout sits under 500ms and the regression this guards sat
at 8.2s, which land in different buckets.
"""

import unittest

from server_metrics import (
    bucket_delta,
    parse_latency_buckets,
    parse_request_counts,
    quantile_from_buckets,
    summarise_window,
)

BOUNDS = ["10", "50", "100", "500", "1000", "5000", "10000", "60000", "300000", "+Inf"]


def metrics(counts, cls="Rrha_no_mt_sync_hnsw_pq", query_type="get_graphql", ok=0, failed=0) -> str:
    """Render cumulative buckets as Weaviate exposes them."""
    lines = []
    for bound, count in zip(BOUNDS, counts):
        lines.append(
            f'queries_durations_ms_bucket{{class_name="{cls}",'
            f'query_type="{query_type}",le="{bound}"}} {count}'
        )
    if ok:
        lines.append(
            f'requests_total{{api="rest",class_name="{cls}",' f'query_type="get",status="ok"}} {ok}'
        )
    if failed:
        lines.append(
            f'requests_total{{api="rest",class_name="{cls}",'
            f'query_type="get",status="failed"}} {failed}'
        )
    return "\n".join(lines)


class TestParsing(unittest.TestCase):
    def test_parses_real_shape(self):
        # the exact numbers a live pod returned after three GraphQL gets
        text = metrics([2, 3, 3, 3, 3, 3, 3, 3, 3, 3])
        buckets = parse_latency_buckets(text)
        self.assertEqual(buckets[10.0], 2.0)
        self.assertEqual(buckets[50.0], 3.0)
        self.assertEqual(buckets[float("inf")], 3.0)
        self.assertAlmostEqual(quantile_from_buckets(buckets, 0.99), 48.8, places=1)

    def test_sums_across_classes_and_pods(self):
        a = metrics([5, 10, 10, 10, 10, 10, 10, 10, 10, 10], cls="A")
        b = metrics([1, 2, 2, 2, 2, 2, 2, 2, 2, 2], cls="B")
        buckets = parse_latency_buckets(a + "\n" + b)
        self.assertEqual(buckets[10.0], 6.0)
        self.assertEqual(buckets[float("inf")], 12.0)

    def test_ignores_other_query_types(self):
        # gRPC queries are not instrumented; nothing else should be counted as if
        # it were the probe's traffic
        text = metrics([9, 9, 9, 9, 9, 9, 9, 9, 9, 9], query_type="aggregate")
        self.assertEqual(parse_latency_buckets(text, "get_graphql"), {})

    def test_request_counts_by_status(self):
        counts = parse_request_counts(metrics([1] * 10, ok=100, failed=7))
        self.assertEqual(counts["ok"], 100.0)
        self.assertEqual(counts["failed"], 7.0)


class TestWindow(unittest.TestCase):
    def test_healthy_window(self):
        before = parse_latency_buckets(metrics([100, 180, 200, 200, 200, 200, 200, 200, 200, 200]))
        # 100 more queries, all under 500ms
        after = parse_latency_buckets(metrics([150, 260, 290, 300, 300, 300, 300, 300, 300, 300]))
        s = summarise_window(before, after, {"ok": 200}, {"ok": 300})
        self.assertEqual(s["queries"], 100)
        self.assertLess(s["p99_ms"], 500)
        self.assertEqual(s["error_rate"], 0.0)

    def test_window_with_a_stall_is_visible(self):
        """
        The production shape: most queries fine, the tail past 5s. A client-side
        p99 over ~20 samples per second missed this; the histogram cannot.
        """
        before = parse_latency_buckets(metrics([100] * 10))
        after = parse_latency_buckets(metrics([150, 180, 185, 190, 190, 192, 200, 200, 200, 200]))
        s = summarise_window(before, after, {"ok": 100}, {"ok": 198, "failed": 2})
        self.assertGreater(s["p99_ms"], 5000)
        self.assertGreater(s["error_rate"], 0)

    def test_counter_reset_is_not_a_negative_delta(self):
        """
        A restarted pod's counters start again at zero. Treating that as a
        negative delta would subtract from the window being measured.
        """
        before = parse_latency_buckets(metrics([500] * 10))
        after = parse_latency_buckets(metrics([7] * 10))
        delta = bucket_delta(before, after)
        self.assertEqual(delta[10.0], 7.0)
        self.assertTrue(all(v >= 0 for v in delta.values()))

    def test_empty_window_has_no_quantile(self):
        self.assertIsNone(quantile_from_buckets({}, 0.99))
        same = parse_latency_buckets(metrics([42] * 10))
        self.assertIsNone(quantile_from_buckets(bucket_delta(same, same), 0.99))


if __name__ == "__main__":
    unittest.main()
