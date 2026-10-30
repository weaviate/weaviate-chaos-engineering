"""
Run with: python3 -m unittest discover -s apps/mt-tenant-activation-latency

The distributions below are the real ones measured on a 3-node multi-tenant
cluster either side of the fix, including the port-forward reconnect that made a
max-based gate a coin toss, and the marginal p99 that made a percentile gate one.
These tests pin that the gate separates the builds and is not moved by either.

Samples carry when they began, because the gate reads medians over time windows.
`serial` lays a list of waits out the way the serial workers produce them: each
call begins when the previous one ended plus the worker's interval, so a slow
cluster stretches the timeline rather than packing more samples into it.
"""

import unittest
from typing import List, Sequence, Tuple

from gates import (
    CREATE_LIMIT,
    QUERY_LIMIT,
    QUERY_P95,
    QUERY_P99,
    WRITE_P95,
    WRITE_P99,
    DeadlineAccumulator,
    Limit,
    Phase,
    RestartWatcher,
    Sample,
    TailLimit,
    UserErrorAccumulator,
    count_deadline_waits,
    evaluate,
    evaluate_churn,
    evaluate_fresh_pod,
    evaluate_sustained,
    evaluate_user_errors,
    percentile,
    sliding_windows,
    stretches,
    summarise,
    thin_windows,
    within,
    within_any,
)


def serial(ms: Sequence[float], ok: bool = True, interval_s: float = 0.2) -> List[Sample]:
    """The waits as one serial worker would have produced them, starting at t=0."""
    out: List[Sample] = []
    at = 0.0
    for m in ms:
        out.append((at, m, ok))
        at += m / 1000.0 + interval_s
    return out


def chain(*runs: Sequence[Sample], interval_s: float = 0.2) -> List[Sample]:
    """Runs placed one after another in time, as one worker would have made them."""
    out: List[Sample] = []
    offset = 0.0
    for run in runs:
        for at, ms, ok in run:
            out.append((at + offset, ms, ok))
        if run:
            last_at, last_ms, _ = run[-1]
            offset += last_at + last_ms / 1000.0 + interval_s
    return out


def spread(fast: float, n_fast: int, slow: float, n_slow: int) -> List[float]:
    """n_slow slow waits spread evenly through n_fast fast ones: a tail, not a stall."""
    out: List[float] = []
    every = max(1, n_fast // max(1, n_slow))
    for i in range(n_fast):
        out.append(fast)
        if n_slow and i % every == 0:
            out.append(slow)
            n_slow -= 1
    return out


QUIET = serial([4.0] * 99)
# before the fix: the stall is pervasive, so p99 itself is ~12s
ROLLING_BEFORE = serial([3.2] * 100 + [11_200.0] * 30 + [12_452.0] * 5)
WRITES_QUIET = serial([110.0] * 150, interval_s=0.1)
WRITES_BEFORE = serial([122.0] * 240 + [7_584.0] * 8, interval_s=0.1)
# after: p99 in the tens of ms, and one 15s outlier from the client's port-forward
# reconnecting mid-roll -- a healthy build that a max-based gate would have failed
ROLLING_AFTER = serial([3.8] * 200 + [15_015.0] + [3.8] * 200 + [12.8] * 4)
WRITES_AFTER = serial([6.4] * 640 + [159.0] * 9, interval_s=0.1)

# The leg that failed a p99 gate by 10% on 2026-10-29: p50 4ms, p99 193ms against a
# 174ms limit, and a write p99 of 663ms against 600ms. Both tails, neither a stall.
ROLLING_MARGINAL = serial(spread(4.0, 1150, 200.0, 24))
WRITES_MARGINAL = serial(spread(112.0, 1180, 700.0, 21), interval_s=0.1)

# The shape the suite exists to catch: every call held while the apply is backed up.
# Forty seconds of 2s calls, between stretches that are fine. The same 30 slow calls
# scattered through the run share its p99 exactly and are not that shape.
STALL = [4.0] * 200 + [2_000.0] * 30 + [4.0] * 200
ROLLING_STALLED = serial(STALL)
ROLLING_SCATTERED = serial(spread(4.0, 400, 2_000.0, 30))

LIMITS = dict(min_queries=30, min_writes=30)


def gate(
    rolling=ROLLING_AFTER,
    writes=WRITES_AFTER,
    deadline_waits=0,
    quiet=QUIET,
    writes_quiet=WRITES_QUIET,
    **kwargs,
):
    """evaluate over a healthy run, with the pieces under test swapped in."""
    limits = dict(LIMITS)
    limits.update(kwargs)
    return evaluate(quiet, rolling, writes_quiet, writes, deadline_waits, **limits)


# Tenant churn as the environment that caught the regression measured it: create
# 0.095s quiet against 3.56s rolling, delete 0.49s against 3.32s. Shaped as the churn
# worker sees them -- about one iteration a second, since an iteration is four trips.
CREATE_QUIET = serial([40.0] * 54 + [60.0] * 12 + [95.0] * 4, interval_s=1.0)
DELETE_QUIET = serial([260.0] * 54 + [330.0] * 12 + [490.0] * 4, interval_s=1.0)
# A rolling window on a healthy build: the same distribution plus the client's
# port-forward reconnecting, which cost 15,015ms on a build known to be fine.
CREATE_ROLLING_OK = serial(
    [40.0] * 75 + [15_015.0] + [40.0] * 75 + [120.0] * 8 + [15_015.0], interval_s=1.0
)
DELETE_ROLLING_OK = serial(
    [260.0] * 75 + [15_015.0] + [260.0] * 75 + [600.0] * 8 + [15_015.0], interval_s=1.0
)
# ... and carrying the regression, which stalled a third of the window.
CREATE_ROLLING_BAD = serial([40.0] * 60 + [3_560.0] * 20, interval_s=1.0)
DELETE_ROLLING_BAD = serial([260.0] * 60 + [3_320.0] * 20, interval_s=1.0)

CHURN_LIMITS = dict(min_churns=20, max_failures=3)


def churn(create=None, delete=None, **kwargs):
    """evaluate_churn over a healthy run, with the pieces under test swapped in."""
    limits = dict(CHURN_LIMITS)
    limits.update(kwargs)
    return evaluate_churn(
        create or Phase(CREATE_QUIET, CREATE_ROLLING_OK),
        delete or Phase(DELETE_QUIET, DELETE_ROLLING_OK),
        **limits,
    )


# The leg with the slowest quiet window the suite has measured: a 40ms median, p99
# 139ms. Against it, a sustained 250ms is 6x; sustained 100ms is inside the band.
AT_REST_WORST = serial([40.0] * 98 + [139.0] * 2)
ROLLING_6X = serial([40.0] * 100 + [250.0] * 300 + [40.0] * 100)
ROLLING_INSIDE_AT_REST = serial([40.0] * 100 + [100.0] * 300 + [40.0] * 100)
# The simulation environment's own numbers: 830-990ms at rest, a 3,540ms stall
# during the rollout. There 5x the quiet median is above the ceiling, and it binds.
SIM_QUIET = serial([850.0] * 98 + [900.0] * 2)
SIM_ROLLING = serial([850.0] * 100 + [3_540.0] * 40 + [850.0] * 100)
# A quiet window that was itself terrible, with a sustained 1.5s roll inside 5x of
# it. A relative bound on its own would wave this through.
ROLLING_OVER_THE_CEILING = serial([40.0] * 100 + [1_500.0] * 60 + [40.0] * 100)


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


def requests_text(
    start: float, ok: int = 1000, user_errors: int = 0, server_errors: int = 0
) -> str:
    """
    One pod's metrics, as the 4xx accounting reads them.

    The user_error child series does not exist until a 4xx happens, which is why
    "exposed" keys on the metric and not on that child.
    """
    s = f"process_start_time_seconds {start}\n"
    s += (
        'requests_total{class_name="MtRolloutLatency",api="rest",'
        f'query_type="objects",status="ok"}} {ok}\n'
    )
    if user_errors:
        s += (
            'requests_total{class_name="MtRolloutLatency",api="rest",'
            f'query_type="objects",status="user_error"}} {user_errors}\n'
        )
    if server_errors:
        s += (
            'requests_total{class_name="MtRolloutLatency",api="rest",'
            f'query_type="objects",status="server_error"}} {server_errors}\n'
        )
    return s


class TestPercentile(unittest.TestCase):
    def test_edges(self):
        self.assertIsNone(percentile([], 0.99))
        self.assertEqual(percentile([7.0], 0.99), 7.0)
        self.assertEqual(percentile([1, 2, 3, 4, 5], 0.0), 1)
        self.assertEqual(percentile([1, 2, 3, 4, 5], 1.0), 5)

    def test_nearest_rank_does_not_round_a_slow_tail_away(self):
        """149 fast queries and two at 10s is 1.3% slow, so the p99 is the slow one."""
        self.assertEqual(percentile([4.0] * 149 + [10_000.0] * 2, 0.99), 10_000.0)

    def test_a_tail_under_one_percent_stays_out_of_p99(self):
        """The other side of the same boundary: 0.5% slow must not move it."""
        self.assertEqual(percentile([4.0] * 199 + [10_000.0], 0.99), 4.0)

    def test_summary_of_empty_is_not_a_crash(self):
        s = summarise([])
        self.assertEqual(s["count"], 0)
        self.assertIsNone(s["max"])


class TestSlidingWindows(unittest.TestCase):
    def test_a_window_begins_on_every_call_with_enough_behind_it(self):
        samples = serial([4.0] * 10, interval_s=1.0)  # one call a second, ten seconds
        windows = sliding_windows(samples, 5.0, 5)
        # a window anchored after the fifth second would run past the last call
        self.assertEqual(len(windows), 5)
        self.assertEqual(windows[0].calls, 5)
        self.assertEqual(windows[0].median_ms, 4.0)

    def test_a_window_too_thin_to_judge_is_skipped_not_passed(self):
        self.assertEqual(sliding_windows(serial([4.0] * 3), 60.0, 5), [])

    def test_a_slow_cluster_makes_fewer_calls_not_more(self):
        """Ten calls at 10s each fill a minute with six of them, which is what the
        default window and minimum are sized for."""
        windows = sliding_windows(serial([10_000.0] * 10), 60.0, 5)
        self.assertTrue(windows)
        self.assertEqual(windows[0].calls, 6)
        self.assertEqual(windows[0].median_ms, 10_000.0)

    def test_the_median_reads_every_call_served_or_not(self):
        samples = chain(serial([4.0] * 3), serial([30_000.0] * 3, ok=False))
        self.assertEqual(sliding_windows(samples, 1.0, 1)[0].median_ms, 4.0)
        self.assertEqual(sliding_windows(samples, 91.0, 6)[0].median_ms, 4.0)
        self.assertEqual(sliding_windows(samples, 91.0, 6)[0].calls, 6)

    def test_a_window_never_runs_past_the_last_call(self):
        """The end of the data is whatever the end happened to be: two hangs after a
        quiet minute would be two of the three calls in a window anchored there."""
        samples = serial([4.0] * 300 + [15_015.0] * 2)
        windows = sliding_windows(samples, 60.0, 5)
        self.assertTrue(windows)
        self.assertLessEqual(max(w.median_ms for w in windows), 4.0)
        self.assertEqual(sliding_windows(samples, 1_000.0, 5), [])

    def test_order_of_arrival_does_not_matter(self):
        samples = serial([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(
            sliding_windows(samples, 60.0, 5), sliding_windows(list(reversed(samples)), 60.0, 5)
        )

    def test_stretches_merge_overlapping_windows(self):
        windows = sliding_windows(serial([4.0] * 10, interval_s=1.0), 5.0, 5)
        self.assertEqual(stretches(windows), [(windows[0].start, windows[-1].end)])
        self.assertEqual(stretches([]), [])
        apart = [windows[0], windows[-1]._replace(start=50.0, end=55.0)]
        self.assertEqual(len(stretches(apart)), 2)


class TestLimit(unittest.TestCase):
    def test_relative_to_the_quiet_median_with_a_floor(self):
        limit = Limit(ratio=5.0, floor_ms=139.0, ceiling_ms=1000.0)
        self.assertEqual(limit.resolve(serial([40.0] * 10))[0], 200.0)
        self.assertEqual(limit.resolve(QUIET)[0], 139.0)
        self.assertIn("5.0x this leg's own quiet median of 4ms", limit.resolve(QUIET)[1])

    def test_the_ceiling_caps_a_terrible_quiet_window(self):
        limit = Limit(ratio=5.0, floor_ms=139.0, ceiling_ms=1000.0)
        self.assertEqual(limit.resolve(serial([60_000.0] * 10))[0], 1000.0)
        self.assertIn("ceiling", limit.resolve(serial([60_000.0] * 10))[1])

    def test_no_quiet_window_is_the_ceiling_not_no_limit(self):
        limit = Limit(ratio=5.0, floor_ms=139.0, ceiling_ms=1000.0)
        self.assertEqual(limit.resolve([])[0], 1000.0)


class TestGate(unittest.TestCase):
    def test_before_the_fix_fails(self):
        passed, lines = gate(ROLLING_BEFORE, WRITES_BEFORE, 3925)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: query median" in l for l in lines))
        self.assertTrue(any("FAILED: write median" in l for l in lines))
        self.assertTrue(any("hit their deadline" in l for l in lines))

    def test_after_the_fix_passes_despite_one_reconnect_outlier(self):
        """The 15s sample is a port-forward reconnect; it must not fail the run."""
        passed, lines = gate(ROLLING_AFTER, WRITES_AFTER, 0)
        self.assertTrue(passed, lines)
        self.assertTrue(any("slowest single query was 15015ms" in l for l in lines))
        self.assertTrue(any("Not gated" in l for l in lines))

    def test_writes_alone_can_fail_a_run(self):
        """Writes are the broadest signal; a clean query window must not excuse them."""
        passed, lines = gate(ROLLING_AFTER, WRITES_BEFORE, 0)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: write median" in l for l in lines))

    def test_the_quiet_window_never_gates(self):
        passed, lines = gate(quiet=serial([60_000.0] * 99))
        self.assertTrue(passed, "only the rolling window gates")
        self.assertTrue(any("queries, quiet" in l for l in lines))

    def test_a_build_without_the_metric_still_gates_on_latency(self):
        passed, _ = gate(ROLLING_BEFORE, WRITES_BEFORE, None)
        self.assertFalse(passed)
        passed, lines = gate(ROLLING_AFTER, WRITES_AFTER, None)
        self.assertTrue(passed)
        self.assertTrue(any("unavailable on this build" in l for l in lines))

    def test_a_rollout_that_measured_nothing_fails(self):
        passed, lines = gate(serial([1.0] * 3))
        self.assertFalse(passed)
        self.assertTrue(any("nothing was measured where it matters" in l for l in lines))

    def test_a_handful_of_fast_writes_cannot_clear_the_write_gate(self):
        """A write worker that barely ran leaves a thin distribution, not a pass."""
        passed, lines = gate(writes=serial([1.0] * 4))
        self.assertFalse(passed)
        self.assertTrue(any("too thin a distribution" in l for l in lines))

    def test_no_samples_at_all_is_reported_as_such(self):
        passed, lines = gate(writes=[])
        self.assertFalse(passed)
        self.assertTrue(any("no write samples at all" in l for l in lines))


class TestSustainedMedianNotPercentile(unittest.TestCase):
    """
    Why the gate moved off the p99. The leg this suite failed on 2026-10-29 had a query
    p99 of 193ms against a 174ms limit and a write p99 of 663ms against 600ms: tails a
    reconnect and a GC pause produce on any build, each ~10% over a limit that had been
    tuned to clear the healthy runs before it. The same week's healthy legs ran 458-663ms
    on the write p99. A limit placed anywhere in that band is crossed by noise about one
    run in two, and placing it above the band would let the regression through.

    The percentile gates were then restored beside the median one, over served calls
    only and looser (TestTailGates): with the median catching a stall, they only have to
    catch a tail out of all proportion, and these legs sit at under half their limits.
    """

    def test_the_marginal_leg_passes(self):
        passed, lines = gate(ROLLING_MARGINAL, WRITES_MARGINAL)
        self.assertTrue(passed, lines)
        self.assertTrue(any("queries, rolling" in l and "p99=200" in l for l in lines))
        self.assertTrue(any("writes,  rolling" in l and "p99=700" in l for l in lines))
        self.assertTrue(any(l.startswith("PASSED: query p99 200ms, under 500ms") for l in lines))
        self.assertTrue(any(l.startswith("PASSED: write p99 700ms, under 1250ms") for l in lines))

    def test_the_marginal_leg_fails_at_the_old_limits(self):
        """What the old limits did to it, kept as the reason the defaults are looser."""
        passed, lines = gate(
            ROLLING_MARGINAL,
            WRITES_MARGINAL,
            query_p99=TailLimit(3.0, 139.0, 1000.0),
            write_p99=TailLimit(0.0, 600.0, 600.0, warn_ms=500.0),
        )
        self.assertFalse(passed)
        self.assertTrue(any(l.startswith("FAILED: query p99 200ms over 139ms") for l in lines))
        self.assertTrue(any(l.startswith("FAILED: write p99 700ms over 600ms") for l in lines))

    def test_a_stall_fails(self):
        """Forty seconds in which every call waits 2s: half of every window in it."""
        passed, lines = gate(ROLLING_STALLED)
        self.assertFalse(passed)
        failed = [l for l in lines if l.startswith("FAILED: query median")]
        self.assertEqual(len(failed), 1, lines)
        self.assertIn("2000ms", failed[0])
        self.assertIn("1 such stretch(es)", failed[0])

    def test_the_same_tail_scattered_passes_the_median_gate(self):
        """The distribution ROLLING_STALLED fails on, rearranged: identical p99, no
        window in which half the calls were slow. A percentile cannot tell these
        apart; the median gate is built to. The restored p99 gate fails both, which
        is the percentile's verdict: 7% of calls at 2s is a tail over any limit."""
        self.assertEqual(
            percentile([ms for _, ms, _ in ROLLING_STALLED], 0.99),
            percentile([ms for _, ms, _ in ROLLING_SCATTERED], 0.99),
        )
        passed, lines = gate(ROLLING_SCATTERED)
        self.assertFalse(any(l.startswith("FAILED: query median") for l in lines), lines)
        self.assertTrue(any(l.startswith("FAILED: query p99 2000ms") for l in lines), lines)
        self.assertFalse(passed)

    def test_a_stall_of_timeouts_is_the_stall_gates_business(self):
        """Calls that each wait out a 30s timeout leave one call per window, which no
        median can be read from. The stall gate counts them instead -- and the run
        still fails."""
        rolling = chain(serial([4.0] * 100), serial([30_000.0] * 10, ok=False))
        passed, lines = gate(rolling)
        self.assertFalse(passed)
        self.assertTrue(any("never served" in l for l in lines))
        self.assertFalse(any("FAILED: query median" in l for l in lines))

    def test_a_roll_too_slow_to_fill_a_window_fails(self):
        """Served calls at 20s each: three to a minute, under the five needed, and
        nothing else to judge. That is not a pass."""
        passed, lines = gate(serial([20_000.0] * 40))
        self.assertFalse(passed)
        self.assertTrue(any("no 60s stretch of the roll held 5 query calls" in l for l in lines))

    def test_the_p99_is_reported_beside_the_verdict(self):
        _, lines = gate()
        self.assertTrue(any("the max is reported, not gated" in l for l in lines))
        self.assertTrue(any("queries, rolling: n=405 p50=4 p99=13 max=15015" in l for l in lines))

    def test_the_window_and_minimum_are_tunable(self):
        passed, _ = gate(ROLLING_STALLED, window_s=10.0, min_window_samples=2)
        self.assertFalse(passed)
        # a window longer than the stall dilutes it below half (the restored p99 gate
        # still fails this run on its tail, which is its own verdict)
        long_writes = serial([6.4] * 1000, interval_s=0.1)
        _, lines = gate(ROLLING_STALLED, writes=long_writes, window_s=100.0)
        self.assertFalse(any(l.startswith("FAILED: query median") for l in lines), lines)

    def test_evaluate_sustained_alone(self):
        passed, lines = evaluate_sustained("query", QUIET, ROLLING_STALLED, QUERY_LIMIT)
        self.assertFalse(passed)
        self.assertIn("across a 60s window", lines[0])
        passed, lines = evaluate_sustained("query", QUIET, ROLLING_AFTER, QUERY_LIMIT)
        self.assertTrue(passed, lines)
        self.assertIn("judged", lines[0])


class TestQueryMedianAgainstItsOwnQuietWindow(unittest.TestCase):
    """
    The limit is relative to the leg's own quiet window: a fixed limit has to clear the
    noisiest runner and then leaves an order of magnitude of room on the others, which is
    more than the 3.6-4.3x degradation that once passed this suite.
    """

    def test_a_degradation_a_fixed_limit_permits_fails(self):
        """6x on a leg whose quiet median was 40ms: 250ms, well under the 1,000ms ceiling."""
        passed, lines = gate(ROLLING_6X, quiet=AT_REST_WORST)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: query median 250ms over 200ms" in l for l in lines))
        self.assertTrue(any("5.0x this leg's own quiet median of 40ms" in l for l in lines))

    def test_the_measured_regression_itself_fails(self):
        """The simulation environment's own numbers, where the ceiling binds."""
        passed, lines = gate(SIM_ROLLING, quiet=SIM_QUIET)
        self.assertFalse(passed)
        self.assertTrue(
            any("FAILED: query median 3540ms over the 1000ms ceiling" in l for l in lines)
        )

    def test_a_terrible_quiet_window_cannot_buy_headroom(self):
        """Why the ceiling stays: a relative bound alone would pass this."""
        passed, lines = gate(ROLLING_OVER_THE_CEILING, quiet=serial([60_000.0] * 99))
        self.assertFalse(passed)
        self.assertTrue(
            any("FAILED: query median 1500ms over the 1000ms ceiling" in l for l in lines)
        )

    def test_the_floor_keeps_the_ratio_out_of_the_noise(self):
        """A 4ms quiet median and 100ms sustained is 25x and still inside the at-rest
        band, so it must pass: a ratio between two single-digit millisecond numbers
        measures the measurement, not the cluster."""
        passed, lines = gate(ROLLING_INSIDE_AT_REST)
        self.assertTrue(passed, lines)
        self.assertTrue(any("floored at 139ms" in l for l in lines))

    def test_the_healthy_leg_still_passes(self):
        passed, lines = gate()
        self.assertTrue(passed, lines)
        self.assertTrue(any("PASSED: query median under 139ms" in l for l in lines))

    def test_an_empty_quiet_window_leaves_the_ceiling_in_place(self):
        """No reference to be relative to is not a reason to stop gating."""
        passed, lines = gate(ROLLING_OVER_THE_CEILING, quiet=[])
        self.assertFalse(passed)
        self.assertTrue(
            any("FAILED: query median 1500ms over the 1000ms ceiling" in l for l in lines)
        )
        passed, _ = gate(ROLLING_6X, quiet=[])
        self.assertTrue(passed, "250ms is under the ceiling and there is no quiet median")

    def test_writes_are_held_to_their_own_quiet_window(self):
        slow = serial([110.0] * 200 + [700.0] * 100 + [110.0] * 200, interval_s=0.1)
        passed, lines = gate(writes=slow)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: write median 700ms over 550ms" in l for l in lines))
        passed, lines = gate(writes=slow, writes_quiet=serial([200.0] * 100, interval_s=0.1))
        self.assertTrue(passed, lines)

    def test_the_knobs_are_tunable(self):
        passed, _ = gate(query_limit=Limit(ratio=0.5, floor_ms=1.0, ceiling_ms=1000.0))
        self.assertFalse(passed, "half of a 4ms quiet median fails a 4ms roll")
        passed, lines = gate(
            ROLLING_6X,
            quiet=AT_REST_WORST,
            query_limit=Limit(100.0, 139.0, 10_000.0),
            # 300 of 500 calls at 250ms is a p95 of 250ms; keep that gate out of a median test
            query_p95=TailLimit(0.0, 500.0, 500.0),
        )
        self.assertTrue(passed, lines)


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
    The waits worth catching are the ones a pod serves while an *earlier* pod is down, so
    they sit on its counter before it restarts in turn. Two scrapes across a rolling
    restart cannot see them: a counter that climbed to 7 and reset reports as zero.
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


def iterations(
    ops_ms: Sequence[Sequence[float]], interval_s: float = 0.5, start: float = 0.0
) -> Tuple[List[Sample], List[Sample]]:
    """
    Tenant churn as the worker runs it: each iteration is create, write, read, delete
    in series, then the interval. Returns the (creates, deletes) the gate samples, so a
    slow iteration spaces its creates by the sum of all four operations, not by one.
    """
    creates: List[Sample] = []
    deletes: List[Sample] = []
    at = start
    for create, write, read, delete in ops_ms:
        creates.append((at, create, True))
        at += (create + write + read) / 1000.0
        deletes.append((at, delete, True))
        at += delete / 1000.0 + interval_s
    return creates, deletes


HEALTHY_ITERATION = (40.0, 110.0, 4.0, 260.0)


class TestThinStretches(unittest.TestCase):
    """
    The reviewer's case on PR 464: skipping windows too thin to judge lets a stall pass
    whenever healthy windows elsewhere qualify, and the slower the stall the thinner the
    windows. Six churn iterations of four 8s operations put a create in any minute at
    most twice, under the five needed, so 170s of that was judged nowhere and passed on
    the 150 healthy iterations either side.
    """

    def test_the_reviewers_stall_now_fails_the_churn_gate(self):
        ops = [HEALTHY_ITERATION] * 75 + [(8_000.0,) * 4] * 6 + [HEALTHY_ITERATION] * 75
        creates, deletes = iterations(ops)
        passed, lines = churn(
            create=Phase(CREATE_QUIET, creates), delete=Phase(DELETE_QUIET, deletes)
        )
        self.assertFalse(passed)
        thin = [l for l in lines if "fewer than the 5 a window needs" in l]
        self.assertEqual(len(thin), 2, lines)
        self.assertTrue(any("tenant create call(s) began" in l for l in thin), thin)
        self.assertTrue(any("tenant delete call(s) began" in l for l in thin), thin)
        self.assertFalse(any(l.startswith("FAILED: tenant create median") for l in lines))

    def test_it_is_the_thin_windows_not_the_judged_ones(self):
        ops = [HEALTHY_ITERATION] * 75 + [(8_000.0,) * 4] * 6 + [HEALTHY_ITERATION] * 75
        creates, _ = iterations(ops)
        judged = sliding_windows(creates, 60.0, 5)
        self.assertTrue(judged)
        self.assertLess(max(w.median_ms for w in judged), 100.0, "every judged window is fine")
        thin = thin_windows(creates, 60.0, 5)
        self.assertTrue(thin)
        self.assertLessEqual(max(w.calls for w in thin), 4)
        self.assertGreater(stretches(thin)[0][1] - stretches(thin)[0][0], 60.0)

    def test_the_regression_that_was_caught_still_fails_on_its_median(self):
        """3.56s creates keep iterations near 8s, so the windows stay judgeable and the
        median gate is the one that fires, as before."""
        ops = [HEALTHY_ITERATION] * 60 + [(3_560.0, 110.0, 4.0, 3_320.0)] * 30
        creates, deletes = iterations(ops)
        passed, lines = churn(
            create=Phase(CREATE_QUIET, creates), delete=Phase(DELETE_QUIET, deletes)
        )
        self.assertFalse(passed)
        self.assertTrue(any(l.startswith("FAILED: tenant create median 3560ms") for l in lines))

    def test_a_healthy_churn_run_has_no_thin_window(self):
        creates, deletes = iterations([HEALTHY_ITERATION] * 300)
        self.assertEqual(thin_windows(creates, 60.0, 5), [])
        passed, lines = churn(
            create=Phase(CREATE_QUIET, creates), delete=Phase(DELETE_QUIET, deletes)
        )
        self.assertTrue(passed, lines)

    def test_two_reconnect_hangs_back_to_back_do_not_thin_a_window(self):
        """Two 15s hangs in a row are 30s of one minute; the other 30s hold the fast
        calls, so no window drops under five."""
        ops = [HEALTHY_ITERATION] * 100 + [(15_015.0, 110.0, 4.0, 260.0)] * 2
        ops += [HEALTHY_ITERATION] * 100
        creates, deletes = iterations(ops)
        self.assertEqual(thin_windows(creates, 60.0, 5), [])
        passed, lines = churn(
            create=Phase(CREATE_QUIET, creates), delete=Phase(DELETE_QUIET, deletes)
        )
        self.assertTrue(passed, lines)

    def test_the_query_and_write_gates_fail_the_same_shape(self):
        """Served calls at 15s each for three minutes: four to a minute, so under the
        floor, and the consistency wait does not bound a stall of this kind."""
        rolling = chain(serial([4.0] * 100), serial([15_000.0] * 12), serial([4.0] * 100))
        passed, lines = gate(rolling)
        self.assertFalse(passed)
        self.assertTrue(any("query call(s) began in the 60s" in l for l in lines), lines)
        writes = chain(
            serial([6.4] * 600, interval_s=0.1),
            serial([15_000.0] * 12, interval_s=0.1),
            serial([6.4] * 600, interval_s=0.1),
        )
        passed, lines = gate(writes=writes)
        self.assertFalse(passed)
        self.assertTrue(any("write call(s) began in the 60s" in l for l in lines), lines)

    def test_a_short_thin_stretch_at_the_end_is_still_not_judged(self):
        """The end-effect that produced a false positive before: two hangs after a quiet
        minute, with nothing after them. Windows must lie inside the span."""
        passed, lines = gate(chain(ROLLING_AFTER, serial([15_015.0] * 2, ok=False)))
        self.assertTrue(passed, lines)

    def test_the_verdict_names_the_stretch(self):
        rolling = chain(serial([4.0] * 100), serial([15_000.0] * 12), serial([4.0] * 100))
        _, lines = gate(rolling)
        thin = [l for l in lines if "query call(s) began" in l][0]
        self.assertIn("1 such stretch(es)", thin)
        self.assertIn("each took most of 12s or more", thin)


class TestTailGates(unittest.TestCase):
    """
    The p95/p99 gates, restored beside the median one over served calls only. What made
    them flake was the calls that got no response: the roll costs the client its
    port-forward, and a refusal, a reset or a timeout arrived with a wait of up to 15s
    that ranked as the slowest 1%. Those are excluded here and gated elsewhere.
    """

    def test_a_healthy_roll_passes_both_tails(self):
        passed, lines = gate()
        self.assertTrue(passed, lines)
        self.assertTrue(any(l.startswith("PASSED: query p95 4ms") for l in lines), lines)
        self.assertTrue(any(l.startswith("PASSED: query p99 13ms") for l in lines), lines)
        self.assertTrue(any(l.startswith("PASSED: write p95 6ms") for l in lines), lines)
        self.assertTrue(any(l.startswith("PASSED: write p99 159ms") for l in lines), lines)

    def test_the_pervasive_stall_fails_the_p99(self):
        passed, lines = gate(ROLLING_BEFORE, WRITES_BEFORE)
        self.assertFalse(passed)
        self.assertTrue(any(l.startswith("FAILED: query p99 12452ms") for l in lines), lines)
        self.assertTrue(any(l.startswith("FAILED: write p99 7584ms") for l in lines), lines)

    def test_no_response_failures_do_not_count_towards_the_tail(self):
        """Calls that waited 25s and raised: four of them would be the slowest 1% of
        this window, and they are left out of the tail. The stall gate still sees them
        (25s is past STALL_MS, and four is more than MAX_STALLED_CALLS)."""
        resets = serial([25_000.0] * 4, ok=False)
        passed, lines = gate(chain(ROLLING_AFTER, resets))
        self.assertTrue(any(l.startswith("PASSED: query p99 13ms") for l in lines), lines)
        self.assertTrue(any("4 failed call(s) excluded" in l for l in lines), lines)
        self.assertFalse(passed, "the stall gate, not the tail, fails four 25s failures")
        self.assertTrue(any("never served" in l for l in lines))

    def test_reconnect_failures_under_the_stall_line_leave_a_passing_run_passing(self):
        """The 15s port-forward reconnects that moved the old p99: failed, so out of
        the tail; under STALL_MS, so only a note. The run passes."""
        resets = serial([15_015.0] * 3, ok=False)
        passed, lines = gate(chain(ROLLING_AFTER, resets))
        self.assertTrue(passed, lines)
        self.assertTrue(any("3 failed call(s) excluded" in l for l in lines), lines)

    def test_instant_refusals_do_not_fill_the_tail_either(self):
        """A refusal at 2ms is not a fast query: it must not pull the p99 down."""
        slow = serial([4.0] * 300 + [900.0] * 6)  # p99 = 900ms over served calls
        refusals = serial([2.0] * 3000, ok=False)
        _, lines = gate(chain(slow, refusals))
        self.assertTrue(any(l.startswith("FAILED: query p99 900ms") for l in lines), lines)

    def test_the_quiet_window_is_served_calls_too(self):
        """A quiet window padded with 15s failures must not buy a 75s limit."""
        quiet = chain(QUIET, serial([15_000.0] * 2, ok=False))
        _, lines = gate(ROLLING_SCATTERED, quiet=quiet)
        self.assertTrue(any(l.startswith("FAILED: query p99 2000ms over 500ms") for l in lines))

    def test_the_query_limit_is_relative_to_the_quiet_p99_floored_and_capped(self):
        """A 139ms quiet p99 allows 695ms; a 900ms one is capped at the 1s ceiling."""
        _, lines = gate(ROLLING_INSIDE_AT_REST, quiet=AT_REST_WORST)
        self.assertTrue(any("query p99 100ms, under 695ms" in l for l in lines), lines)
        _, lines = gate(SIM_ROLLING, quiet=SIM_QUIET)
        self.assertTrue(any("query p99 3540ms over the 1000ms ceiling" in l for l in lines))

    def test_the_write_p99_warns_in_its_tolerance_band(self):
        writes = serial([110.0] * 500 + [1_100.0] * 10, interval_s=0.1)
        passed, lines = gate(writes=writes)
        self.assertTrue(passed, lines)
        self.assertTrue(any("warning: write p99 1100ms over the 1000ms target" in l for l in lines))

    def test_the_write_p95_can_fail_on_its_own(self):
        """Six percent of writes at 800ms: p95 over its 750ms limit, p99 inside 1250ms."""
        writes = serial([110.0] * 470 + [800.0] * 30, interval_s=0.1)
        passed, lines = gate(writes=writes)
        self.assertFalse(passed)
        self.assertTrue(any(l.startswith("FAILED: write p95 800ms over 750ms") for l in lines))
        self.assertTrue(any(l.startswith("PASSED: write p99 800ms") for l in lines), lines)

    def test_the_limits_are_tunable(self):
        passed, _ = gate(query_p99=TailLimit(0.0, 10.0, 10.0))
        self.assertFalse(passed, "a 10ms query p99 limit fails the healthy run")
        _, lines = gate(ROLLING_BEFORE, query_p99=TailLimit(0.0, 20_000.0, 20_000.0))
        self.assertTrue(any(l.startswith("PASSED: query p99 12452ms") for l in lines), lines)
        self.assertEqual(
            (QUERY_P99.ratio, QUERY_P99.floor_ms, QUERY_P99.ceiling_ms), (5, 500, 1000)
        )
        self.assertEqual((WRITE_P99.ceiling_ms, WRITE_P99.warn_ms), (1250, 1000))
        self.assertEqual(
            (QUERY_P95.floor_ms, QUERY_P95.ceiling_ms, WRITE_P95.ceiling_ms), (250, 500, 750)
        )

    def test_no_quiet_window_gates_on_the_ceiling(self):
        _, lines = gate(quiet=[])
        self.assertTrue(any("query p99 13ms, under 1000ms" in l for l in lines), lines)


class TestFailedCallsDoNotPassAsLatency(unittest.TestCase):
    """
    A refused call is not a request the cluster served. Counting refusals as
    served samples lets an instant connection refusal fill min_queries and hold
    the median down, so they are kept out of the served count and gated on their share.
    """

    def test_a_window_of_refusals_cannot_satisfy_min_queries(self):
        passed, lines = gate(chain(serial([1.0] * 3), serial([1.0] * 500, ok=False)))
        self.assertFalse(passed)
        self.assertTrue(any("nothing was measured where it matters" in l for l in lines))

    def test_mostly_failing_queries_fail_the_run(self):
        passed, lines = gate(chain(ROLLING_AFTER, serial([1.0] * 500, ok=False)))
        self.assertFalse(passed)
        self.assertTrue(any("query calls failed" in l and "FAILED" in l for l in lines))

    def test_mostly_failing_writes_fail_the_run(self):
        passed, lines = gate(writes=chain(WRITES_AFTER, serial([1.0] * 10_000, ok=False)))
        self.assertFalse(passed)
        self.assertTrue(any("write calls failed" in l and "FAILED" in l for l in lines))

    def test_a_handful_of_reconnect_refusals_only_gets_a_note(self):
        passed, lines = gate(chain(ROLLING_AFTER, serial([2.0] * 4, ok=False)))
        self.assertTrue(passed, lines)
        self.assertTrue(any("note:" in l and "query calls failed" in l for l in lines))

    def test_the_allowance_is_tunable(self):
        passed, _ = gate(chain(ROLLING_AFTER, serial([2.0] * 4, ok=False)), max_failure_ratio=0.0)
        self.assertFalse(passed)


class TestTimeoutsAreNotRefusals(unittest.TestCase):
    """
    A refusal that came back instantly and a request that waited out its timeout are both
    failures, and only the duration tells them apart. Dropping the slow one hides the stall.
    """

    def test_a_few_timeouts_among_fast_queries_fail_the_run(self):
        """Copilot's case: 100 fast queries and three 30s timeouts."""
        rolling = chain(serial([4.0] * 100), serial([30_000.0] * 3, ok=False))
        passed, lines = gate(rolling, deadline_waits=None)
        self.assertFalse(passed)
        self.assertTrue(any("never served" in l for l in lines))

    def test_their_waits_reach_the_windows_too(self):
        """Not only the stall count: a timed-out call is in the window it began in, so
        a stretch of them among served calls fails the median gate as well."""
        stalled = [(at, 9_000.0, i % 2 == 0) for i, (at, _, _) in enumerate(serial([9_000.0] * 12))]
        rolling = chain(serial([4.0] * 100), stalled, serial([4.0] * 100))
        passed, lines = gate(rolling, max_stalled_calls=99, stall_ms=9_000.0)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: query median 9000ms" in l for l in lines))

    def test_timed_out_writes_fail_the_run(self):
        writes = chain(serial([6.4] * 600, interval_s=0.1), serial([90_000.0] * 3, ok=False))
        passed, lines = gate(writes=writes, deadline_waits=None)
        self.assertFalse(passed)
        self.assertTrue(any("never served" in l and "write" in l for l in lines))

    def test_one_slow_reconnect_failure_is_still_an_artifact(self):
        """15,015ms was measured on a healthy build, so it must not gate alone."""
        passed, lines = gate(chain(ROLLING_AFTER, serial([15_015.0], ok=False)))
        self.assertTrue(passed, lines)

    def test_quick_refusals_are_not_counted_as_stalls(self):
        passed, lines = gate(chain(ROLLING_AFTER, serial([2.0] * 20, ok=False)))
        self.assertTrue(passed, lines)
        self.assertFalse(any("never served" in l for l in lines))


class TestCollectionProblemsFailTheRun(unittest.TestCase):
    """
    A run whose numbers are otherwise spotless must still fail when something went wrong
    collecting them, since each of those makes the latency look good for the wrong reason.
    """

    def test_a_problem_fails_an_otherwise_clean_run(self):
        passed, lines = gate(problems=["kubectl rollout restart exited 1: forbidden"])
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: kubectl rollout restart exited 1" in l for l in lines))

    def test_every_problem_is_reported_not_just_the_first(self):
        passed, lines = gate(problems=["one", "two"])
        self.assertFalse(passed)
        self.assertTrue(any(l == "FAILED: one" for l in lines))
        self.assertTrue(any(l == "FAILED: two" for l in lines))


class TestTenantChurnGate(unittest.TestCase):
    """
    The gap this closes: the suite's only lifecycle call was the seed, before the roll, so
    a build whose tenant create p99 went 0.095s -> 3.56s under a rollout passed every leg.
    """

    def test_a_healthy_rollout_passes(self):
        passed, lines = churn()
        self.assertTrue(passed, lines)
        self.assertTrue(any("PASSED: tenant create median" in l for l in lines))
        self.assertTrue(any("PASSED: tenant delete median" in l for l in lines))

    def test_the_quiet_window_is_reported_next_to_the_rolling_one(self):
        """A reader has to be able to see the before and the after in the log."""
        _, lines = churn()
        quiet = [l for l in lines if l.startswith("tenant create, quiet")]
        rolling = [l for l in lines if l.startswith("tenant create, rolling")]
        self.assertEqual(len(quiet), 1, lines)
        self.assertEqual(len(rolling), 1, lines)
        self.assertIn("p99=95", quiet[0])
        self.assertIn("p99=15015", rolling[0])

    def test_the_quiet_window_never_gates(self):
        passed, _ = churn(create=Phase(serial([60_000.0] * 70), CREATE_ROLLING_OK))
        self.assertTrue(passed, "only the rolling window gates")

    def test_a_create_regression_fails_the_run(self):
        passed, lines = churn(create=Phase(CREATE_QUIET, CREATE_ROLLING_BAD))
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: tenant create median 3560ms" in l for l in lines))
        self.assertFalse(any("FAILED: tenant delete" in l for l in lines))

    def test_a_delete_regression_fails_the_run(self):
        """Gated separately: deletes are ~5x slower at baseline, so a combined
        limit would have to clear theirs and a 37x create regression would fit
        inside it."""
        passed, lines = churn(delete=Phase(DELETE_QUIET, DELETE_ROLLING_BAD))
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: tenant delete median 3320ms" in l for l in lines))
        self.assertFalse(any("FAILED: tenant create" in l for l in lines))

    def test_a_create_regression_hidden_under_the_delete_limit_still_fails(self):
        """1.4s creates, under what a delete may reach: the exact shape of the miss."""
        bad = serial([40.0] * 60 + [1_400.0] * 30, interval_s=1.0)
        passed, lines = churn(create=Phase(CREATE_QUIET, bad))
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: tenant create median 1400ms over 500ms" in l for l in lines))

    def test_failed_iterations_are_counted_and_gated(self):
        passed, lines = churn(failures=9, attempts=60)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: 9 of 60 tenant-churn iterations" in l for l in lines))

    def test_a_couple_of_failed_iterations_only_gets_a_note(self):
        passed, lines = churn(failures=2, attempts=60)
        self.assertTrue(passed, lines)
        self.assertTrue(any("note: 2 of 60 tenant-churn iterations" in l for l in lines))

    def test_the_failure_allowance_is_tighter_than_the_query_one(self):
        """MAX_FAILURE_RATIO passes 50% failed calls. Half the tenant creates
        failing is not a pass, so churn counts its own."""
        half = dict(failures=30, attempts=60)
        passed, _ = gate()
        self.assertTrue(passed, "the existing gates see nothing wrong")
        passed, lines = churn(**half)
        self.assertFalse(passed)

    def test_a_churn_worker_that_barely_ran_cannot_pass(self):
        """Few samples is what a percentile gate reads as a clean run."""
        passed, lines = churn(create=Phase(CREATE_QUIET, serial([40.0] * 4, interval_s=1.0)))
        self.assertFalse(passed)
        self.assertTrue(any("tenant creates were served" in l for l in lines))

    def test_deletes_that_all_failed_cannot_pass_as_no_samples(self):
        failed = serial([30_000.0] * 40, ok=False, interval_s=1.0)
        passed, lines = churn(delete=Phase(DELETE_QUIET, failed))
        self.assertFalse(passed)
        self.assertTrue(any("tenant deletes were served" in l for l in lines))
        self.assertTrue(
            any("no 60s stretch of the roll held 5 tenant delete calls" in l for l in lines)
        )

    def test_a_window_with_no_churn_at_all_fails(self):
        """The state this change exists to end: no lifecycle call in the window."""
        passed, lines = churn(create=Phase(CREATE_QUIET, []), delete=Phase(DELETE_QUIET, []))
        self.assertFalse(passed)
        self.assertTrue(any("no tenant create samples at all" in l for l in lines))
        self.assertTrue(any("no tenant delete samples at all" in l for l in lines))

    def test_a_timed_out_create_joins_the_window_it_stalled(self):
        """A create that waited 30s and raised measured the stall; dropping it
        would leave only the fast ones that came before."""
        rolling = chain(
            serial([40.0] * 60, interval_s=1.0), serial([9_000.0] * 20, ok=False, interval_s=1.0)
        )
        passed, lines = churn(create=Phase(CREATE_QUIET, rolling))
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: tenant create median 9000ms" in l for l in lines))

    def test_two_reconnect_artifacts_do_not_fail_a_healthy_build(self):
        """At these sample counts the nearest-rank p99 is the slowest sample, and
        15,015ms of it was a port-forward reconnect on a build known to be fine.
        Two of them in two minutes are still two calls among a hundred."""
        rolling = serial([40.0] * 30 + [15_015.0] * 2 + [40.0] * 30, interval_s=1.0)
        passed, lines = churn(create=Phase(CREATE_QUIET, rolling))
        self.assertTrue(passed, lines)
        self.assertEqual(percentile([ms for _, ms, _ in rolling], 0.99), 15_015.0)
        self.assertTrue(any("max=15015" in l for l in lines))

    def test_the_regressions_own_window_is_a_stall_not_a_tail(self):
        """The regression stalled a third of its five-minute window. In a window
        sized for the churn worker that third is contiguous and fills every window
        inside it; the same third scattered across the run would be a tail, which
        the p90 printed beside the gate still shows and the gate does not fail on."""
        scattered = serial(spread(40.0, 40, 3_560.0, 20), interval_s=1.0)
        passed, lines = churn(create=Phase(CREATE_QUIET, scattered))
        self.assertTrue(passed, lines)
        self.assertTrue(any("tenant create, rolling" in l and "p90=3560" in l for l in lines))

    def test_the_limits_are_tunable(self):
        passed, _ = churn(create_limit=Limit(ratio=0.5, floor_ms=1.0, ceiling_ms=10_000.0))
        self.assertFalse(passed)
        passed, _ = churn(
            create=Phase(CREATE_QUIET, CREATE_ROLLING_BAD),
            create_limit=Limit(ratio=100.0, floor_ms=5_000.0, ceiling_ms=10_000.0),
        )
        self.assertTrue(passed)
        self.assertEqual(CREATE_LIMIT.floor_ms, 500.0)


class TestUserErrorsAreGated(unittest.TestCase):
    """
    What escaped here: the candidate logged 107 client-visible 4xx against the baseline's
    39 while server_errors stayed at 0.00% on both sides, and nothing counted them.
    """

    def test_the_candidate_count_fails(self):
        passed, lines = evaluate_user_errors(1, 15, 9)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: the cluster answered 15 requests" in l for l in lines))
        self.assertTrue(any("107 of these against the baseline's 39" in l for l in lines))

    def test_the_top_of_the_healthy_range_passes(self):
        """The baseline ran 1-9 per leg, so 9 must not fail."""
        passed, lines = evaluate_user_errors(0, 9, 9)
        self.assertTrue(passed, lines)
        self.assertTrue(any("PASSED: 9 client-visible 4xx" in l for l in lines))

    def test_the_overlap_is_not_pretended_away(self):
        """The ranges overlap at 4-9, so a leg in there passes on purpose: a
        threshold that tried to separate every leg would be a flaky one."""
        for n in (4, 5, 6, 7, 8, 9):
            passed, _ = evaluate_user_errors(0, n, 9)
            self.assertTrue(passed, f"{n} is inside the healthy range")
        for n in (10, 12, 15):
            passed, _ = evaluate_user_errors(0, n, 9)
            self.assertFalse(passed, f"{n} is above every healthy leg measured")

    def test_zero_passes_and_says_so(self):
        passed, lines = evaluate_user_errors(0, 0, 9)
        self.assertTrue(passed, lines)
        self.assertTrue(any("PASSED: 0 client-visible 4xx" in l for l in lines))

    def test_the_quiet_window_is_reported_and_never_gates(self):
        passed, lines = evaluate_user_errors(50, 0, 9)
        self.assertTrue(passed, "only the rolling window gates")
        self.assertTrue(any(l == "4xx, quiet:   50" for l in lines))
        self.assertTrue(any(l == "4xx, rolling: 0" for l in lines))

    def test_an_unmeasurable_window_is_not_a_failure_here(self):
        """A build without the metric, or a scrape that could not be completed.
        The latter is a collection problem and fails the run on its own; this
        gate must not claim to have judged it either way."""
        passed, lines = evaluate_user_errors(None, None, 9)
        self.assertTrue(passed, lines)
        self.assertTrue(any("4xx unavailable for this run" in l for l in lines))

    def test_the_threshold_is_tunable(self):
        passed, _ = evaluate_user_errors(0, 5, 4)
        self.assertFalse(passed)
        passed, _ = evaluate_user_errors(0, 15, 100)
        self.assertTrue(passed)


class TestUserErrorAccounting(unittest.TestCase):
    """
    requests_total resets with the process just like the deadline counter, and the 4xx
    worth catching are the ones a pod answers while an *earlier* pod is down.
    """

    def test_a_cluster_with_no_4xx_reports_zero_not_unknown(self):
        t = requests_text(100.0)
        acc = UserErrorAccumulator({"w": t})
        acc.observe({"w": t})
        self.assertEqual(acc.total(), 0)

    def test_a_build_without_the_metric_is_unknown(self):
        acc = UserErrorAccumulator({"w": "go_goroutines 42\n"})
        acc.observe({"w": "go_goroutines 43\n"})
        self.assertIsNone(acc.total())

    def test_the_delta_when_the_pod_did_not_restart(self):
        acc = UserErrorAccumulator({"w": requests_text(100.0, user_errors=5)})
        acc.observe({"w": requests_text(100.0, user_errors=12)})
        self.assertEqual(acc.total(), 7)

    def test_sampling_keeps_the_4xx_from_before_a_restart(self):
        acc = UserErrorAccumulator({"w": requests_text(100.0)})
        acc.observe({"w": requests_text(100.0, user_errors=7)})
        acc.observe({"w": requests_text(900.0)})  # restarted, counters back to zero
        self.assertEqual(acc.total(), 7)

    def test_summed_across_pods(self):
        before = {"a": requests_text(1.0), "b": requests_text(2.0)}
        after = {
            "a": requests_text(1.0, user_errors=2),
            "b": requests_text(2.0, user_errors=3),
        }
        acc = UserErrorAccumulator(before)
        acc.observe(after)
        self.assertEqual(acc.total(), 5)

    def test_server_errors_are_not_folded_in(self):
        """They stayed at 0.00% on both sides of the regression; conflating the
        two would make this gate unable to say what it measured."""
        acc = UserErrorAccumulator({"w": requests_text(100.0)})
        acc.observe({"w": requests_text(100.0, user_errors=3, server_errors=40)})
        self.assertEqual(acc.total(), 3)

    def test_a_partial_scrape_is_not_read_as_a_restart(self):
        """Reading one as a restart would add the pod's whole lifetime counter.
        RestartWatcher already treats it this way."""
        acc = UserErrorAccumulator({"w": requests_text(100.0, user_errors=5)})
        acc.observe({"w": 'requests_total{status="user_error"} 6\n'})
        self.assertEqual(acc.total(), 1)

    def test_a_restart_after_a_partial_scrape_is_still_seen(self):
        """The partial scrape must not erase the start time the next is compared against."""
        acc = UserErrorAccumulator({"w": requests_text(100.0, user_errors=5)})
        acc.observe({"w": 'requests_total{status="user_error"} 6\n'})
        acc.observe({"w": requests_text(200.0, user_errors=2)})
        self.assertEqual(acc.total(), 3)

    def test_the_label_order_is_not_relied_on(self):
        acc = UserErrorAccumulator({"w": "process_start_time_seconds 1\n"})
        acc.observe({"w": 'requests_total{status="user_error",api="rest"} 4\n'})
        self.assertEqual(acc.total(), 4)

    def test_other_metrics_whose_name_ends_in_requests_total_are_ignored(self):
        acc = UserErrorAccumulator({"w": requests_text(1.0)})
        acc.observe(
            {
                "w": requests_text(1.0)
                + 'weaviate_module_requests_total{status="user_error"} 99\n'
                + 'tokenizer_requests_total{status="user_error"} 99\n'
            }
        )
        self.assertEqual(acc.total(), 0)


class TestFreshPodWindow(unittest.TestCase):
    """
    A restarted pod reports ready with lazy shard stubs, so the first query per tenant pays
    a shard load -- which the suite used to average into a window minutes long. See
    evaluate_fresh_pod for why the client side cannot carry a latency threshold on it.
    """

    FRESH = serial([40.0] * 200 + [120.0] * 25)

    def test_a_measured_window_passes_and_is_reported(self):
        passed, lines = evaluate_fresh_pod(Phase(QUIET, self.FRESH), 3, 15.0, 30)
        self.assertTrue(passed, lines)
        self.assertTrue(any(l.startswith("fresh pod, at rest:") for l in lines))
        self.assertTrue(any("fresh pod, 15s after ready:" in l for l in lines))
        self.assertTrue(any("3 pod(s) came back" in l for l in lines))
        self.assertTrue(any("Reported, not gated on latency" in l for l in lines))

    def test_a_roll_in_which_no_pod_was_seen_coming_back_fails(self):
        """Every pod's process restarts in a rolling restart, so observing none
        of them means the scrape was measured, not the cluster."""
        passed, lines = evaluate_fresh_pod(Phase(QUIET, self.FRESH), 0, 15.0, 30)
        self.assertFalse(passed)
        self.assertTrue(any("no pod was observed coming back" in l for l in lines))

    def test_a_window_with_nothing_in_it_fails(self):
        passed, lines = evaluate_fresh_pod(Phase(QUIET, []), 3, 15.0, 30)
        self.assertFalse(passed)
        self.assertTrue(any("went unmeasured" in l for l in lines))

    def test_failed_calls_do_not_fill_the_minimum(self):
        """Same rule as everywhere else here: a refusal measured no serving."""
        rolling = chain(serial([4.0] * 10), serial([30_000.0] * 400, ok=False))
        passed, lines = evaluate_fresh_pod(Phase(QUIET, rolling), 3, 15.0, 30)
        self.assertFalse(passed)
        self.assertTrue(any("only 10 queries were served" in l for l in lines))

    def test_no_sampler_is_reported_as_unavailable(self):
        passed, lines = evaluate_fresh_pod(Phase(QUIET, []), None, 15.0, 30)
        self.assertTrue(passed, "the sampler failing is already a collection problem")
        self.assertTrue(any("fresh-pod window unavailable" in l for l in lines))

    def test_a_slow_fresh_window_is_caught_by_the_query_gate_not_this_one(self):
        """
        A fresh pod that stalls every query for ninety seconds is inside the rolling
        window too, and that gate reads it against the leg's own quiet window.
        """
        slow = serial([40.0] * 200 + [3_540.0] * 25)
        passed, _ = evaluate_fresh_pod(Phase(AT_REST_WORST, slow), 3, 15.0, 30)
        self.assertTrue(passed, "this gate only says the window was measured")
        rolling = chain(serial([40.0] * 975), slow)
        passed, lines = gate(rolling, quiet=AT_REST_WORST)
        self.assertFalse(passed, "but the leg does not pass")
        self.assertTrue(any("FAILED: query median 3540ms" in l for l in lines))


class TestRestartWatcher(unittest.TestCase):
    """
    The timestamps the fresh-pod window is cut from. process_start_time_seconds
    changes only when the process does and it is in the same scrape the deadline
    counter is read from, so this costs no extra kubectl.
    """

    def test_a_pod_coming_back_opens_a_window(self):
        w = RestartWatcher({"a": metrics_text(100.0)})
        w.observe({"a": metrics_text(100.0)}, 10.0)
        self.assertEqual(w.windows(15.0), [])
        w.observe({"a": metrics_text(900.0)}, 20.0)
        self.assertEqual(w.windows(15.0), [(20.0, 35.0)])

    def test_each_pod_is_timed_once(self):
        w = RestartWatcher({p: metrics_text(1.0) for p in ("a", "b", "c")})
        for pod, at in (("a", 10.0), ("b", 30.0), ("c", 50.0)):
            w.observe({pod: metrics_text(900.0)}, at)
            w.observe({pod: metrics_text(900.0)}, at + 5.0)
        self.assertEqual(w.windows(15.0), [(10.0, 25.0), (30.0, 45.0), (50.0, 65.0)])

    def test_a_pod_missing_from_a_sample_is_not_a_restart(self):
        """Mid-roll a pod cannot be scraped; it must not open a window on its
        way back in with the start time it already had."""
        w = RestartWatcher({"a": metrics_text(1.0), "b": metrics_text(2.0)})
        w.observe({"a": metrics_text(1.0)}, 10.0)
        w.observe({"a": metrics_text(1.0), "b": metrics_text(2.0)}, 20.0)
        self.assertEqual(w.windows(15.0), [])

    def test_a_pod_absent_from_the_baseline_counts_as_fresh(self):
        w = RestartWatcher({})
        w.observe({"a": metrics_text(900.0)}, 10.0)
        self.assertEqual(w.windows(15.0), [(10.0, 25.0)])

    def test_a_scrape_without_a_start_time_is_left_alone(self):
        """Not every scrape is a whole metrics page; a truncated one must not
        read as a restart, nor erase the start time already held."""
        w = RestartWatcher({"a": metrics_text(100.0)})
        w.observe({"a": "go_goroutines 42\n"}, 10.0)
        w.observe({"a": metrics_text(100.0)}, 20.0)
        self.assertEqual(w.windows(15.0), [])


class TestWindows(unittest.TestCase):
    def test_within_keeps_the_calls_that_began_inside_the_window(self):
        samples = [(10.0, 5.0, True), (11.0, 30_000.0, False), (99.0, 4.0, True)]
        self.assertEqual(within(samples, 0.0, 15.0), samples[:2])
        self.assertEqual(within(samples, 10.0, 10.0), samples[:1])

    def test_overlapping_windows_take_a_sample_once(self):
        """Pods restart closer together than a window is long."""
        samples = [(10.0, 5.0, True), (12.0, 7.0, True)]
        self.assertEqual(within_any(samples, [(0.0, 15.0), (8.0, 23.0)]), samples)

    def test_failures_keep_their_durations(self):
        samples = [(10.0, 5.0, True), (11.0, 30_000.0, False), (99.0, 4.0, True)]
        self.assertEqual(within_any(samples, [(0.0, 15.0)]), samples[:2])

    def test_no_windows_selects_nothing(self):
        self.assertEqual(within_any([(1.0, 2.0, True)], []), [])


if __name__ == "__main__":
    unittest.main()
