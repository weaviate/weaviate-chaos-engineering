"""
Tests for reading query latency and error rate out of Weaviate's metrics.

Run with: python3 -m unittest discover -s apps/restart-highly-available-qps-benchmark

The metrics text below is the real format, bounds included: 10, 50, 100, 500,
1000, 5000, 10000, 60000, 300000, +Inf. Those brackets are what make this worth
reading -- a healthy rollout sits under 500ms and the regression this guards sat
at 8.2s, which land in different buckets.

The status label values are the real ones too. RequestStatus.String() emits
"ok", "user_error" and "server_error" and nothing else; an earlier version of
this gate watched for status="failed", a value Weaviate never writes, so the
error-rate check could not fire no matter how badly the cluster behaved. The
window tests below pin that, along with the per-pod accounting that keeps a pod
which did not restart from donating its lifetime history to the window.
"""

import unittest

from server_metrics import (
    WindowAccumulator,
    bucket_delta,
    parse_latency_buckets,
    parse_request_counts,
    parse_start_time,
    quantile_from_buckets,
    snapshot_pods,
    summarise_window,
    sustained_breach,
    sustained_limit,
)

BOUNDS = ["10", "50", "100", "500", "1000", "5000", "10000", "60000", "300000", "+Inf"]


def metrics(
    counts,
    cls="Rrha_no_mt_sync_hnsw_pq",
    query_type="get_graphql",
    ok=0,
    user_error=0,
    server_error=0,
    start_time=None,
) -> str:
    """Render cumulative buckets and request counters as Weaviate exposes them."""
    lines = []
    for bound, count in zip(BOUNDS, counts):
        lines.append(
            f'queries_durations_ms_bucket{{class_name="{cls}",'
            f'query_type="{query_type}",le="{bound}"}} {count}'
        )
    for status, value in (
        ("ok", ok),
        ("user_error", user_error),
        ("server_error", server_error),
    ):
        if value:
            lines.append(
                f'requests_total{{api="rest",class_name="{cls}",'
                f'query_type="get",status="{status}"}} {value}'
            )
    if start_time is not None:
        lines.append(f"process_start_time_seconds {start_time}")
    return "\n".join(lines)


def flat(count, **kwargs) -> str:
    """Metrics text whose buckets all hold the same cumulative count."""
    return metrics([count] * len(BOUNDS), **kwargs)


class TestParsing(unittest.TestCase):
    def test_parses_real_shape(self):
        # the exact numbers a live pod returned after three GraphQL gets
        text = metrics([2, 3, 3, 3, 3, 3, 3, 3, 3, 3])
        buckets = parse_latency_buckets(text)
        self.assertEqual(buckets[10.0], 2.0)
        self.assertEqual(buckets[50.0], 3.0)
        self.assertEqual(buckets[float("inf")], 3.0)
        self.assertAlmostEqual(quantile_from_buckets(buckets, 0.99), 48.8, places=1)

    def test_sums_across_classes_and_shards(self):
        a = metrics([5, 10, 10, 10, 10, 10, 10, 10, 10, 10], cls="A")
        b = metrics([1, 2, 2, 2, 2, 2, 2, 2, 2, 2], cls="B")
        buckets = parse_latency_buckets(a + "\n" + b)
        self.assertEqual(buckets[10.0], 6.0)
        self.assertEqual(buckets[float("inf")], 12.0)

    def test_ignores_other_query_types(self):
        # aggregates are not in the histogram; nothing else should be counted as
        # if it were the probe's traffic
        text = flat(9, query_type="aggregate")
        self.assertEqual(parse_latency_buckets(text, "get_graphql"), {})

    def test_request_counts_by_status(self):
        counts = parse_request_counts(flat(1, ok=100, user_error=3, server_error=7))
        self.assertEqual(counts["ok"], 100.0)
        self.assertEqual(counts["user_error"], 3.0)
        self.assertEqual(counts["server_error"], 7.0)

    def test_start_time(self):
        self.assertEqual(parse_start_time(flat(1, start_time=1759000000.5)), 1759000000.5)
        # a pod that does not expose it must not be read as "started at zero",
        # which would look like a restart on every scrape
        self.assertIsNone(parse_start_time(flat(1)))


class TestWindow(unittest.TestCase):
    def test_healthy_window(self):
        before = snapshot_pods(
            {
                "w-0": metrics(
                    [100, 180, 200, 200, 200, 200, 200, 200, 200, 200],
                    ok=200,
                    start_time=1000.0,
                )
            }
        )
        # 100 more queries, all under 500ms
        after = snapshot_pods(
            {
                "w-0": metrics(
                    [150, 260, 290, 300, 300, 300, 300, 300, 300, 300],
                    ok=300,
                    start_time=1000.0,
                )
            }
        )
        s = summarise_window(before, after)
        self.assertEqual(s["queries"], 100)
        self.assertLess(s["p99_ms"], 500)
        self.assertEqual(s["error_rate"], 0.0)
        self.assertEqual(s["requests"], 100)
        self.assertEqual(s["pods"], 1)
        self.assertEqual(s["pods_restarted"], 0)

    def test_window_with_a_stall_is_visible(self):
        """
        The production shape: most queries fine, the tail past 5s. A client-side
        p99 over one second's samples missed this; the histogram cannot.
        """
        before = snapshot_pods({"w-0": flat(100, ok=100, start_time=1000.0)})
        after = snapshot_pods(
            {
                "w-0": metrics(
                    [150, 180, 185, 190, 190, 192, 200, 200, 200, 200],
                    ok=198,
                    server_error=2,
                    start_time=1000.0,
                )
            }
        )
        s = summarise_window(before, after)
        self.assertGreater(s["p99_ms"], 5000)
        self.assertGreater(s["error_rate"], 0)
        self.assertEqual(s["server_errors"], 2)

    def test_counter_reset_is_not_a_negative_delta(self):
        """
        A restarted pod's counters start again at zero. Treating that as a
        negative delta would subtract from the window being measured.
        """
        before = parse_latency_buckets(flat(500))
        after = parse_latency_buckets(flat(7))
        delta = bucket_delta(before, after)
        self.assertEqual(delta[10.0], 7.0)
        self.assertTrue(all(v >= 0 for v in delta.values()))

    def test_empty_window_has_no_quantile(self):
        self.assertIsNone(quantile_from_buckets({}, 0.99))
        same = parse_latency_buckets(flat(42))
        self.assertIsNone(quantile_from_buckets(bucket_delta(same, same), 0.99))

    def test_surviving_pod_does_not_dilute_the_window(self):
        """
        Three pods at 1000 each; two restart to 50, the third keeps serving and
        reaches 1100. Only 200 queries happened in the window.

        Summing all pods and then differencing read before=3000, after=1200,
        and -- because 1200 is below the baseline -- reported the whole 1200 as
        window traffic, importing the surviving pod's entire lifetime. Per-pod
        snapshots are what make this 200.
        """
        before = snapshot_pods({f"w-{i}": flat(1000, ok=1000, start_time=100.0) for i in range(3)})
        after = snapshot_pods(
            {
                "w-0": flat(50, ok=50, start_time=200.0),
                "w-1": flat(50, ok=50, start_time=201.0),
                "w-2": flat(1100, ok=1100, start_time=100.0),
            }
        )
        s = summarise_window(before, after)
        self.assertEqual(s["queries"], 200)  # the summed-then-differenced bug read 1200
        self.assertEqual(s["requests"], 200)
        self.assertEqual(s["pods"], 3)
        self.assertEqual(s["pods_restarted"], 2)
        self.assertEqual(s["pods_missing"], 0)

    def test_restart_past_the_baseline_is_not_subtracted(self):
        """
        The case the backwards-delta heuristic is blind to: the pod restarted and
        then served *more* than its pre-restart total, so the counters look like
        ordinary growth. Only process_start_time_seconds reveals it, and without
        that the window would be understated by the whole baseline.
        """
        before = snapshot_pods({"w-0": flat(1000, ok=1000, start_time=100.0)})
        after = snapshot_pods({"w-0": flat(1500, ok=1500, start_time=900.0)})
        s = summarise_window(before, after)
        self.assertEqual(s["queries"], 1500)  # not 500
        self.assertEqual(s["requests"], 1500)
        self.assertEqual(s["pods_restarted"], 1)

    def test_error_rate_counts_server_errors_only(self):
        """
        A user_error is a rejected query, not a broken cluster. Folding it into
        error_rate would let one malformed request fail a rollout, so it is
        reported on its own instead.
        """
        before = snapshot_pods({"w-0": flat(0, start_time=1000.0)})
        after = snapshot_pods({"w-0": flat(1000, ok=900, user_error=100, start_time=1000.0)})
        s = summarise_window(before, after)
        self.assertEqual(s["error_rate"], 0.0)
        self.assertEqual(s["user_errors"], 100)
        self.assertEqual(s["server_errors"], 0)
        self.assertEqual(s["requests"], 1000)

    def test_error_rate_denominator_includes_every_status(self):
        before = snapshot_pods({"w-0": flat(0, start_time=1000.0)})
        after = snapshot_pods(
            {"w-0": flat(100, ok=80, user_error=10, server_error=10, start_time=1000.0)}
        )
        s = summarise_window(before, after)
        self.assertEqual(s["requests"], 100)
        self.assertAlmostEqual(s["error_rate"], 0.1)

    def test_the_failed_status_label_is_dead(self):
        """
        Pins the original bug. status="failed" is not a value Weaviate ever
        emits -- RequestStatus.String() has only ok/user_error/server_error --
        so a gate written against it reads zero errors through any outage. If
        this ever stops being 0.0 the label set changed and the gate needs
        rewriting, not this test relaxing.
        """
        dead = (
            'requests_total{api="rest",class_name="C",query_type="get",status="failed"} 50\n'
            'requests_total{api="rest",class_name="C",query_type="get",status="ok"} 950\n'
            "process_start_time_seconds 1000.0"
        )
        counts = parse_request_counts(dead)
        self.assertEqual(counts["failed"], 50.0)  # parsed, but never scored

        before = snapshot_pods({"w-0": flat(0, start_time=1000.0)})
        s = summarise_window(before, snapshot_pods({"w-0": dead}))
        self.assertEqual(s["error_rate"], 0.0)
        self.assertEqual(s["server_errors"], 0)

    def test_pod_that_came_up_during_the_window_counts_in_full(self):
        """A pod absent from `before` has no baseline, so all of it is window."""
        before = snapshot_pods({"w-0": flat(1000, ok=1000, start_time=100.0)})
        after = snapshot_pods(
            {
                "w-0": flat(1200, ok=1200, start_time=100.0),
                "w-1": flat(70, ok=70, start_time=900.0),
            }
        )
        s = summarise_window(before, after)
        self.assertEqual(s["queries"], 270)
        self.assertEqual(s["pods"], 2)
        self.assertEqual(s["pods_restarted"], 1)

    def test_pod_missing_at_the_end_is_reported(self):
        """
        Its traffic cannot be recovered -- there is no final counter to read --
        so the run is told the sample is incomplete rather than handed a guess.
        """
        before = snapshot_pods(
            {
                "w-0": flat(1000, ok=1000, start_time=100.0),
                "w-1": flat(1000, ok=1000, start_time=100.0),
            }
        )
        after = snapshot_pods({"w-0": flat(1100, ok=1100, start_time=100.0)})
        s = summarise_window(before, after)
        self.assertEqual(s["queries"], 100)
        self.assertEqual(s["pods"], 1)
        self.assertEqual(s["pods_missing"], 1)
        self.assertEqual(s["pods_restarted"], 0)

    def test_pod_without_a_start_time_falls_back_to_the_heuristic(self):
        """Older builds expose no start time; a plain reset must still not go negative."""
        before = snapshot_pods({"w-0": flat(1000, ok=1000)})
        after = snapshot_pods({"w-0": flat(40, ok=40)})
        s = summarise_window(before, after)
        self.assertEqual(s["queries"], 40)
        self.assertEqual(s["requests"], 40)
        self.assertEqual(s["pods_restarted"], 0)  # unprovable, so not claimed

    def test_no_requests_leaves_the_error_rate_unknown(self):
        """
        requests_total is REST/GraphQL only, so a window can hold gRPC queries
        and no requests at all. A rate of 0.0 there would clear MAX_ERROR_RATE
        on a gate that measured nothing, so it is None and the caller has to
        decide what to do about it.
        """
        before = snapshot_pods({"w-0": flat(0, start_time=1000.0)})
        after = snapshot_pods({"w-0": flat(1000, start_time=1000.0)})
        s = summarise_window(before, after)
        self.assertEqual(s["queries"], 1000)
        self.assertEqual(s["requests"], 0)
        self.assertIsNone(s["error_rate"])


class TestRollingRestartAccumulation(unittest.TestCase):
    """
    A rolling restart resets every pod, so differencing a baseline against a
    final scrape keeps only what each pod served after its own restart -- the
    healthy tail. The stalls happen before the resets, which is exactly what the
    gate is for, so the window has to be sampled while it is open.
    """

    # 1000 fast queries already served when the window opens
    BASELINE = {pod: flat(1000, ok=1000, start_time=100.0) for pod in ("w-0", "w-1", "w-2")}

    # each pod then serves 100 queries past 5s, still on its original process
    STALLED = {
        pod: metrics(
            [1000, 1000, 1000, 1000, 1000, 1000, 1100, 1100, 1100, 1100],
            ok=1100,
            start_time=100.0,
        )
        for pod in ("w-0", "w-1", "w-2")
    }

    # all three come back on a new process with only fast traffic since
    RESTARTED = {pod: flat(50, ok=50, start_time=200.0) for pod in ("w-0", "w-1", "w-2")}

    def test_sampling_keeps_the_stalls_the_restart_would_erase(self):
        acc = WindowAccumulator(snapshot_pods(self.BASELINE))
        acc.observe(snapshot_pods(self.STALLED))
        acc.observe(snapshot_pods(self.RESTARTED))
        s = acc.summary()

        # 300 stalled queries plus 150 fast ones after the restarts
        self.assertEqual(s["queries"], 450)
        self.assertEqual(s["pods_restarted"], 3)
        self.assertGreater(s["p99_ms"], 5000)

    def test_two_snapshots_lose_them(self):
        """The failure mode the accumulator exists to avoid, pinned."""
        s = summarise_window(snapshot_pods(self.BASELINE), snapshot_pods(self.RESTARTED))
        self.assertEqual(s["queries"], 150)  # the 300 stalled queries are gone
        self.assertLess(s["p99_ms"], 100)  # and the window looks healthy

    def test_a_pod_missing_from_one_sample_resumes_on_the_next(self):
        """A restarting pod cannot be scraped; that must not drop the others."""
        acc = WindowAccumulator(snapshot_pods(self.BASELINE))
        acc.observe(snapshot_pods({p: t for p, t in self.STALLED.items() if p != "w-2"}))
        acc.observe(snapshot_pods(self.RESTARTED))
        s = acc.summary()
        # w-0 and w-1 contributed 100 stalls each; w-2's were lost with its reset,
        # which is the bounded loss the sampling interval buys down
        self.assertEqual(s["queries"], 350)
        self.assertGreater(s["p99_ms"], 5000)

    def test_steady_state_does_not_double_count(self):
        acc = WindowAccumulator(snapshot_pods(self.BASELINE))
        acc.observe(snapshot_pods(self.BASELINE))
        acc.observe(snapshot_pods(self.BASELINE))
        s = acc.summary()
        self.assertEqual(s["queries"], 0)
        self.assertEqual(s["pods_restarted"], 0)
        self.assertIsNone(s["error_rate"])


class TestIntervalMedians(unittest.TestCase):
    """
    The gate reads the window one scrape at a time. A healthy interval holds a few
    hundred queries under 100ms; the interval a stall lands in holds everything the
    stall held, at the stall's latency, so its median is seconds. The p99 over the
    whole window cannot tell a stall from a restart's scattered slow tail; this can.
    """

    POD = "w-0"

    def cumulative(self, fast: int, slow: int) -> str:
        # `fast` queries under 100ms and `slow` over 5s, cumulative over the process
        return metrics([fast] * 6 + [fast + slow] * 4, start_time=1.0)

    def test_each_scrape_is_its_own_interval(self):
        acc = WindowAccumulator(snapshot_pods({self.POD: self.cumulative(1000, 0)}))
        acc.observe(snapshot_pods({self.POD: self.cumulative(1300, 0)}))
        acc.observe(snapshot_pods({self.POD: self.cumulative(1310, 300)}))
        acc.observe(snapshot_pods({self.POD: self.cumulative(1610, 300)}))
        medians = acc.interval_medians(min_queries=20)
        self.assertEqual(len(medians), 3)
        self.assertLess(medians[0], 100)
        self.assertGreater(medians[1], 5000)
        self.assertLess(medians[2], 100)

    def test_a_starved_interval_is_unknown_not_fast(self):
        acc = WindowAccumulator(snapshot_pods({self.POD: self.cumulative(1000, 0)}))
        acc.observe(snapshot_pods({self.POD: self.cumulative(1003, 0)}))
        self.assertEqual(acc.interval_medians(min_queries=20), [None])
        self.assertLess(acc.interval_medians(min_queries=3)[0], 10.0)

    def test_a_restarted_pods_interval_is_its_whole_counter(self):
        acc = WindowAccumulator(snapshot_pods({self.POD: self.cumulative(1000, 0)}))
        restarted = metrics([0, 0, 0, 0, 0, 0, 40, 40, 40, 40], start_time=2.0)
        acc.observe(snapshot_pods({self.POD: restarted}))
        self.assertGreater(acc.interval_medians(min_queries=20)[0], 5000)

    def test_the_whole_window_p99_cannot_tell_the_two_apart(self):
        """The same 300 slow queries, in one interval or spread over thirty: identical
        p99 over the window, and only the first is a stall."""
        stalled = WindowAccumulator(snapshot_pods({self.POD: self.cumulative(1000, 0)}))
        for i in range(30):
            stalled.observe(snapshot_pods({self.POD: self.cumulative(1000 + 300 * (i + 1), 0)}))
        stalled.observe(snapshot_pods({self.POD: self.cumulative(10_000, 300)}))

        spread = WindowAccumulator(snapshot_pods({self.POD: self.cumulative(1000, 0)}))
        for i in range(30):
            spread.observe(
                snapshot_pods({self.POD: self.cumulative(1000 + 290 * (i + 1), 10 * (i + 1))})
            )
        spread.observe(snapshot_pods({self.POD: self.cumulative(10_000, 300)}))

        self.assertEqual(stalled.summary()["p99_ms"], spread.summary()["p99_ms"])
        self.assertIsNotNone(sustained_breach(stalled.interval_medians(20), 500.0))
        self.assertIsNone(sustained_breach(spread.interval_medians(20), 500.0))


class TestIntervalCoverage(unittest.TestCase):
    """
    The reviewer's case on PR 464: a pod that missed scrapes without restarting keeps
    its last sample as `prev`, so its next delta spans several intervals. Folded into
    the current interval, a thousand fast completions it caught up on outvote two
    hundred slow ones from the pods that were there, and a stalled interval's median
    reads as fast. A catch-up delta belongs to the window, not to any interval.
    """

    def cumulative(self, fast: int, slow: int, start: float = 1.0) -> str:
        # `fast` queries under 100ms and `slow` over 5s, cumulative over the process
        return metrics([fast] * 6 + [fast + slow] * 4, start_time=start)

    def test_a_returning_pods_catch_up_cannot_swamp_a_stalled_interval(self):
        acc = WindowAccumulator(
            snapshot_pods({"w-0": self.cumulative(1000, 0), "w-1": self.cumulative(1000, 0)})
        )
        # interval 1: w-1 is unreachable; w-0 serves 300 fast
        acc.observe(snapshot_pods({"w-0": self.cumulative(1300, 0)}))
        # interval 2: w-0 completes 200 queries the stall held; w-1 is back, 1000 fast
        # since its last sample, most of them from interval 1
        acc.observe(
            snapshot_pods({"w-0": self.cumulative(1300, 200), "w-1": self.cumulative(2000, 0)})
        )
        medians = acc.interval_medians(min_queries=20)
        self.assertLess(medians[0], 100)
        self.assertGreater(medians[1], 5000, "the stall, not w-1's thousand fast ones")
        self.assertEqual(acc.catch_ups, [{}, {"w-1": 2}])
        # the whole window still holds everything, including the catch-up
        self.assertEqual(acc.summary()["queries"], 1500)

    def test_the_same_traffic_sampled_every_scrape_lands_in_its_intervals(self):
        """Without the gap, w-1's thousand are split across both intervals as they
        completed, and the stalled interval's median is what the pods served then."""
        acc = WindowAccumulator(
            snapshot_pods({"w-0": self.cumulative(1000, 0), "w-1": self.cumulative(1000, 0)})
        )
        acc.observe(
            snapshot_pods({"w-0": self.cumulative(1300, 0), "w-1": self.cumulative(1900, 0)})
        )
        acc.observe(
            snapshot_pods({"w-0": self.cumulative(1300, 200), "w-1": self.cumulative(2000, 0)})
        )
        medians = acc.interval_medians(min_queries=20)
        self.assertLess(medians[0], 100)
        self.assertGreater(medians[1], 5000)
        self.assertEqual(acc.catch_ups, [{}, {}])

    def test_a_pod_back_on_the_very_next_scrape_is_current(self):
        acc = WindowAccumulator(snapshot_pods({"w-0": self.cumulative(1000, 0)}))
        acc.observe(snapshot_pods({"w-0": self.cumulative(1300, 0)}))
        acc.observe(snapshot_pods({"w-0": self.cumulative(1600, 0)}))
        self.assertEqual(acc.catch_ups, [{}, {}])
        self.assertEqual([max(b.values()) for b in acc.intervals], [300, 300])

    def test_a_restart_after_missed_scrapes_is_a_catch_up_too(self):
        """Counters since a new process started cover an unknown stretch when the pod
        was also unreachable, so they are the window's but no interval's."""
        acc = WindowAccumulator(snapshot_pods({"w-0": self.cumulative(1000, 0)}))
        acc.observe({})
        acc.observe(snapshot_pods({"w-0": self.cumulative(400, 0, start=2.0)}))
        self.assertEqual(acc.catch_ups, [{}, {"w-0": 2}])
        self.assertEqual(acc.intervals, [{}, {}])
        self.assertEqual(acc.summary()["queries"], 400)
        self.assertEqual(acc.summary()["pods_restarted"], 1)

    def test_a_restart_seen_on_consecutive_scrapes_stays_in_its_interval(self):
        """The common case: the pod came up within this interval, so all of it is current."""
        acc = WindowAccumulator(snapshot_pods({"w-0": self.cumulative(1000, 0)}))
        acc.observe(snapshot_pods({"w-0": self.cumulative(0, 40, start=2.0)}))
        self.assertEqual(acc.catch_ups, [{}])
        self.assertGreater(acc.interval_medians(min_queries=20)[0], 5000)

    def test_a_pod_first_seen_mid_window_is_a_catch_up(self):
        acc = WindowAccumulator(snapshot_pods({"w-0": self.cumulative(1000, 0)}))
        acc.observe(snapshot_pods({"w-0": self.cumulative(1300, 0)}))
        acc.observe(
            snapshot_pods({"w-0": self.cumulative(1600, 0), "w-9": self.cumulative(500, 0)})
        )
        self.assertEqual(acc.catch_ups, [{}, {"w-9": 2}])
        self.assertEqual(max(acc.intervals[1].values()), 300)
        self.assertEqual(acc.summary()["queries"], 1100)


class TestSustainedBreach(unittest.TestCase):
    def test_one_slow_interval_is_a_breach_by_default(self):
        self.assertEqual(sustained_breach([40.0, 8_200.0, 40.0], 500.0), (1, [8_200.0]))

    def test_nothing_over_the_limit_is_none(self):
        self.assertIsNone(sustained_breach([40.0, 400.0, 499.0], 500.0))
        self.assertIsNone(sustained_breach([], 500.0))
        self.assertIsNone(sustained_breach([None, None], 500.0))

    def test_a_run_of_several_steps_over_unknown_intervals(self):
        """An interval starved by the stall sits between two it filled; that is the
        stall continuing, not ending."""
        medians = [40.0, 8_200.0, None, 7_000.0, 40.0]
        self.assertEqual(sustained_breach(medians, 500.0, run=2), (1, [8_200.0, 7_000.0]))
        self.assertIsNone(sustained_breach([40.0, 8_200.0, 40.0, 7_000.0], 500.0, run=2))


class TestSustainedLimit(unittest.TestCase):
    def test_relative_with_a_floor(self):
        self.assertEqual(sustained_limit(40.0, 5.0, 500.0, 2000.0)[0], 500.0)
        limit, why = sustained_limit(150.0, 5.0, 500.0, 2000.0)
        self.assertEqual(limit, 750.0)
        self.assertIn("5.0x this run's own quiet median of 150ms", why)

    def test_the_ceiling_caps_a_terrible_quiet_window(self):
        limit, why = sustained_limit(900.0, 5.0, 500.0, 2000.0)
        self.assertEqual(limit, 2000.0)
        self.assertIn("ceiling", why)

    def test_no_quiet_window_is_the_ceiling(self):
        self.assertEqual(sustained_limit(None, 5.0, 500.0, 2000.0)[0], 2000.0)


class TestQuantiles(unittest.TestCase):
    def test_bounds(self):
        # all ten queries landed in the 100ms bucket
        buckets = {10.0: 0.0, 50.0: 0.0, 100.0: 10.0, 500.0: 10.0, float("inf"): 10.0}
        cases = [
            ("p50", 0.50, 75.0),
            ("p99", 0.99, 99.5),
        ]
        for name, q, expected in cases:
            with self.subTest(name):
                self.assertAlmostEqual(quantile_from_buckets(buckets, q), expected, places=1)

    def test_inf_overflow_is_capped_at_the_largest_bound(self):
        # a query slower than 300s is only known to exceed it; reporting the
        # bound understates rather than inventing a number
        buckets = {10.0: 1.0, 300000.0: 1.0, float("inf"): 10.0}
        self.assertEqual(quantile_from_buckets(buckets, 0.99), 300000.0)


if __name__ == "__main__":
    unittest.main()
