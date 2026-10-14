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
    Phase,
    RestartWatcher,
    UserErrorAccumulator,
    count_deadline_waits,
    evaluate,
    evaluate_churn,
    evaluate_fresh_pod,
    evaluate_user_errors,
    percentile,
    summarise,
    within_any,
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

# Tenant churn as the environment that caught the regression measured it: create
# 0.095s quiet against 3.56s rolling, delete 0.49s against 3.32s. Shaped as the churn
# worker sees them -- tens of samples per window, since an iteration is four trips.
CREATE_QUIET = [40.0] * 54 + [60.0] * 12 + [95.0] * 4
DELETE_QUIET = [260.0] * 54 + [330.0] * 12 + [490.0] * 4
# A rolling window on a healthy build: the same distribution plus the client's
# port-forward reconnecting, which cost 15,015ms on a build known to be fine.
CREATE_ROLLING_OK = [40.0] * 50 + [120.0] * 8 + [15_015.0] * 2
DELETE_ROLLING_OK = [260.0] * 50 + [600.0] * 8 + [15_015.0] * 2
# ... and carrying the regression, which stalled a third of the window.
CREATE_ROLLING_BAD = [40.0] * 40 + [3_560.0] * 20
DELETE_ROLLING_BAD = [260.0] * 40 + [3_320.0] * 20

CHURN_LIMITS = dict(
    max_create_ms=1000,
    max_delete_ms=1500,
    min_churns=20,
    max_failures=3,
)


def churn(create=None, delete=None, **kwargs):
    """evaluate_churn over a healthy run, with the pieces under test swapped in."""
    limits = dict(CHURN_LIMITS)
    limits.update(kwargs)
    return evaluate_churn(
        create or Phase(CREATE_QUIET, CREATE_ROLLING_OK),
        delete or Phase(DELETE_QUIET, DELETE_ROLLING_OK),
        **limits,
    )


# Query p99 either side of the gate that let the regression through, as this suite's
# own legs measure it: 43-139ms at rest, so the 1,000ms ceiling alone leaves 7-20x of
# room -- more than the 3.6-4.3x degradation that escaped.
AT_REST_WORST = [40.0] * 98 + [139.0] * 2  # p99 139ms, the top of the at-rest band
ROLLING_3_6X = [40.0] * 980 + [500.0] * 20  # p99 500ms: 3.6x of 139ms, under 1,000ms
ROLLING_INSIDE_AT_REST = [40.0] * 980 + [100.0] * 20  # p99 100ms, 25x of a 4ms quiet
# The simulation environment's own numbers: 830-990ms at rest, a 3,540ms peak
# during the rollout. There the ratio works out at 2.5-3s and the ceiling binds.
SIM_QUIET = [300.0] * 98 + [900.0] * 2
SIM_ROLLING = [850.0] * 980 + [3_540.0] * 20
# A quiet window that was itself terrible, with a rolling p99 inside 3x of it. A
# relative bound on its own would wave this through.
ROLLING_OVER_THE_CEILING = [40.0] * 980 + [1_500.0] * 20


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
    """One pod's metrics, as the 4xx accounting reads them.

    requests_total is a gauge with four labels, and the user_error child series
    does not exist until a 4xx happens -- which is why "exposed" has to key on the
    metric and not on that child.
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

    def test_the_slow_tail_reaches_the_gate(self):
        passed, lines = evaluate(QUIET, [4.0] * 149 + [10_000.0] * 2, WRITES_AFTER, 0, **LIMITS)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: query p99" in l for l in lines))

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
    served samples lets an instant connection refusal fill min_queries and hold
    p99 down, so they are kept out of the served count and gated on their share.
    """

    def test_a_window_of_refusals_cannot_satisfy_min_queries(self):
        passed, lines = evaluate(
            QUIET, [1.0] * 3, WRITES_AFTER, 0, **LIMITS, query_failed_ms=[1.0] * 500
        )
        self.assertFalse(passed)
        self.assertTrue(any("nothing was measured where it matters" in l for l in lines))

    def test_mostly_failing_queries_fail_the_run(self):
        passed, lines = evaluate(
            QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS, query_failed_ms=[1.0] * 400
        )
        self.assertFalse(passed)
        self.assertTrue(any("query calls failed" in l and "FAILED" in l for l in lines))

    def test_mostly_failing_writes_fail_the_run(self):
        passed, lines = evaluate(
            QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS, write_failed_ms=[1.0] * 10_000
        )
        self.assertFalse(passed)
        self.assertTrue(any("write calls failed" in l and "FAILED" in l for l in lines))

    def test_a_handful_of_reconnect_refusals_only_gets_a_note(self):
        passed, lines = evaluate(
            QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS, query_failed_ms=[2.0] * 4
        )
        self.assertTrue(passed, lines)
        self.assertTrue(any("note:" in l and "query calls failed" in l for l in lines))

    def test_the_allowance_is_tunable(self):
        passed, _ = evaluate(
            QUIET,
            ROLLING_AFTER,
            WRITES_AFTER,
            0,
            **LIMITS,
            query_failed_ms=[2.0] * 4,
            max_failure_ratio=0.0,
        )
        self.assertFalse(passed)


class TestTimeoutsAreNotRefusals(unittest.TestCase):
    """
    A refusal that came back instantly and a request that waited out its timeout
    are both failures, and only the duration tells them apart. Dropping the slow
    one hides the stall it proves: the deadline metric can be unavailable, the
    refusal share can be tiny, and the served samples that remain are all fast.
    """

    def test_a_few_timeouts_among_fast_queries_fail_the_run(self):
        """Copilot's case: 100 fast queries and three 30s timeouts."""
        passed, lines = evaluate(
            QUIET, [4.0] * 100, WRITES_AFTER, None, **LIMITS, query_failed_ms=[30_000.0] * 3
        )
        self.assertFalse(passed)
        self.assertTrue(any("never served" in l for l in lines))

    def test_their_waits_reach_the_percentile_too(self):
        """Not only the stall count: the waits are in the distribution p99 gates."""
        passed, lines = evaluate(
            QUIET,
            [4.0] * 100,
            WRITES_AFTER,
            None,
            **LIMITS,
            query_failed_ms=[30_000.0] * 3,
            max_stalled_calls=99,
        )
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: query p99" in l for l in lines))

    def test_timed_out_writes_fail_the_run(self):
        passed, lines = evaluate(
            QUIET, ROLLING_AFTER, [6.4] * 600, None, **LIMITS, write_failed_ms=[90_000.0] * 3
        )
        self.assertFalse(passed)
        self.assertTrue(any("never served" in l and "write" in l for l in lines))

    def test_one_slow_reconnect_failure_is_still_an_artifact(self):
        """15,015ms was measured on a healthy build, so it must not gate alone."""
        passed, lines = evaluate(
            QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS, query_failed_ms=[15_015.0]
        )
        self.assertTrue(passed, lines)

    def test_quick_refusals_are_not_counted_as_stalls(self):
        passed, lines = evaluate(
            QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS, query_failed_ms=[2.0] * 20
        )
        self.assertTrue(passed, lines)
        self.assertFalse(any("never served" in l for l in lines))


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


class TestTenantChurnGate(unittest.TestCase):
    """
    The gap this closes: the suite had one tenant-lifecycle call, the seed, and it
    ran before the roll. Nothing was created or deleted while the cluster rolled,
    so the build whose tenant create p99 went 0.095s -> 3.56s and delete
    0.49s -> 3.32s under a rollout passed 18/18 legs across three rounds.
    """

    def test_a_healthy_rollout_passes(self):
        passed, lines = churn()
        self.assertTrue(passed, lines)
        self.assertTrue(any("PASSED: tenant create p90" in l for l in lines))
        self.assertTrue(any("PASSED: tenant delete p90" in l for l in lines))

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
        passed, _ = churn(create=Phase([60_000.0] * 70, CREATE_ROLLING_OK))
        self.assertTrue(passed, "only the rolling window gates")

    def test_a_create_regression_fails_the_run(self):
        passed, lines = churn(create=Phase(CREATE_QUIET, CREATE_ROLLING_BAD))
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: tenant create p90" in l for l in lines))
        self.assertFalse(any("FAILED: tenant delete" in l for l in lines))

    def test_a_delete_regression_fails_the_run(self):
        """Gated separately: deletes are ~5x slower at baseline, so a combined
        limit would have to clear 490ms and a 37x create regression would fit
        inside it."""
        passed, lines = churn(delete=Phase(DELETE_QUIET, DELETE_ROLLING_BAD))
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: tenant delete p90" in l for l in lines))
        self.assertFalse(any("FAILED: tenant create" in l for l in lines))

    def test_a_create_regression_hidden_under_the_delete_limit_still_fails(self):
        """3.56s creates under a 1.5s delete limit: the exact shape of the miss."""
        passed, lines = churn(create=Phase(CREATE_QUIET, [40.0] * 40 + [1_400.0] * 20))
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: tenant create p90" in l for l in lines))

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
        passed, _ = evaluate(QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS)
        self.assertTrue(passed, "the existing gates see nothing wrong")
        passed, lines = churn(**half)
        self.assertFalse(passed)

    def test_a_churn_worker_that_barely_ran_cannot_pass(self):
        """Few samples is what a percentile gate reads as a clean run."""
        passed, lines = churn(create=Phase(CREATE_QUIET, [40.0] * 4))
        self.assertFalse(passed)
        self.assertTrue(any("tenant creates were served" in l for l in lines))

    def test_deletes_that_all_failed_cannot_pass_as_no_samples(self):
        passed, lines = churn(delete=Phase(DELETE_QUIET, [], [30_000.0] * 40))
        self.assertFalse(passed)
        self.assertTrue(any("tenant deletes were served" in l for l in lines))
        self.assertTrue(any("FAILED: tenant delete p90" in l for l in lines))

    def test_a_window_with_no_churn_at_all_fails(self):
        """The state this change exists to end: no lifecycle call in the window."""
        passed, lines = churn(create=Phase(CREATE_QUIET, []), delete=Phase(DELETE_QUIET, []))
        self.assertFalse(passed)
        self.assertTrue(any("no tenant create samples at all" in l for l in lines))
        self.assertTrue(any("no tenant delete samples at all" in l for l in lines))

    def test_a_timed_out_create_joins_the_distribution_it_stalled(self):
        """A create that waited 30s and raised measured the stall; dropping it
        would leave only the fast ones that came before."""
        passed, lines = churn(create=Phase(CREATE_QUIET, [40.0] * 40, [30_000.0] * 20))
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: tenant create p90" in l for l in lines))

    def test_two_reconnect_artifacts_do_not_fail_a_healthy_build(self):
        """Why a p90 and not a p99: at these sample counts the nearest-rank p99 is
        the slowest sample, and 15,015ms of it was a port-forward reconnect on a
        build known to be fine."""
        rolling = [40.0] * 58 + [15_015.0] * 2
        passed, lines = churn(create=Phase(CREATE_QUIET, rolling))
        self.assertTrue(passed, lines)
        self.assertEqual(percentile(rolling, 0.99), 15_015.0, "p99 would have failed it")
        self.assertTrue(any("max=15015" in l for l in lines))

    def test_the_limits_are_tunable(self):
        passed, _ = churn(max_create_ms=10.0)
        self.assertFalse(passed)
        passed, _ = churn(create=Phase(CREATE_QUIET, CREATE_ROLLING_BAD), max_create_ms=10_000.0)
        self.assertTrue(passed)


class TestQueryP99AgainstItsOwnQuietWindow(unittest.TestCase):
    """
    What the ceiling alone could not say. "During rollout, query p99 under 1
    second, and the customer should not see that a rollout happened": 1,000ms
    against the 43-139ms these legs measure at rest is 7-20x of headroom, which is
    more than the 3.6-4.3x degradation (830-990ms at rest, a 3,540ms rollout peak)
    that passed this suite 18/18. The rolling p99 is now bounded by the leg's own
    quiet window as well as by the ceiling, whichever is lower.
    """

    def test_a_degradation_the_ceiling_permits_now_fails(self):
        """3.6x on a leg whose quiet p99 was 139ms: 500ms, well under 1,000ms."""
        self.assertLess(
            percentile(ROLLING_3_6X, 0.99),
            LIMITS["max_query_p99_ms"],
            "the fixed ceiling would have passed this, which is the whole bug",
        )
        passed, lines = evaluate(AT_REST_WORST, ROLLING_3_6X, WRITES_AFTER, 0, **LIMITS)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: query p99 500ms" in l for l in lines))
        self.assertTrue(any("3.0x this leg's own quiet p99 of 139ms" in l for l in lines))

    def test_the_measured_regression_itself_fails(self):
        """The simulation environment's own numbers, where the ceiling binds."""
        passed, lines = evaluate(SIM_QUIET, SIM_ROLLING, WRITES_AFTER, 0, **LIMITS)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: query p99 3540ms over 1000ms" in l for l in lines))

    def test_a_terrible_quiet_window_cannot_buy_headroom(self):
        """Why the ceiling stays: a relative bound alone would pass this."""
        quiet = [60_000.0] * 99
        self.assertLess(
            percentile(ROLLING_OVER_THE_CEILING, 0.99),
            percentile(quiet, 0.99) * 3.0,
            "3x of a 60s quiet window is a minute, so the ratio sees nothing",
        )
        passed, lines = evaluate(quiet, ROLLING_OVER_THE_CEILING, WRITES_AFTER, 0, **LIMITS)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: query p99 1500ms over 1000ms" in l for l in lines))

    def test_the_floor_keeps_the_ratio_out_of_the_noise(self):
        """A 4ms quiet p99 and 100ms rolling is 25x and still inside the at-rest
        band, so it must pass: a ratio between two single-digit millisecond
        numbers measures the measurement, not the cluster."""
        self.assertGreater(percentile(ROLLING_INSIDE_AT_REST, 0.99), percentile(QUIET, 0.99) * 3.0)
        passed, lines = evaluate(QUIET, ROLLING_INSIDE_AT_REST, WRITES_AFTER, 0, **LIMITS)
        self.assertTrue(passed, lines)
        self.assertTrue(any("floored at 139ms" in l for l in lines))

    def test_the_healthy_leg_still_passes(self):
        """The reconnect outlier case again, now against the tighter limit."""
        passed, lines = evaluate(QUIET, ROLLING_AFTER, WRITES_AFTER, 0, **LIMITS)
        self.assertTrue(passed, lines)
        self.assertTrue(any("PASSED: query p99 13ms, under 139ms" in l for l in lines))

    def test_an_empty_quiet_window_leaves_the_ceiling_in_place(self):
        """No reference to be relative to is not a reason to stop gating."""
        passed, lines = evaluate([], ROLLING_OVER_THE_CEILING, WRITES_AFTER, 0, **LIMITS)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: query p99 1500ms over 1000ms" in l for l in lines))
        passed, _ = evaluate([], ROLLING_3_6X, WRITES_AFTER, 0, **LIMITS)
        self.assertTrue(passed, "500ms is under the ceiling and there is no quiet p99")

    def test_the_writes_gate_is_untouched_by_the_ratio(self):
        """Writes keep their absolute limit; only the query gate went relative."""
        passed, lines = evaluate(AT_REST_WORST, ROLLING_3_6X, WRITES_BEFORE, 0, **LIMITS)
        self.assertFalse(passed)
        self.assertTrue(any("FAILED: write p99 7584ms over 500ms" in l for l in lines))

    def test_both_knobs_are_tunable(self):
        passed, _ = evaluate(
            QUIET,
            ROLLING_AFTER,
            WRITES_AFTER,
            0,
            **LIMITS,
            max_query_p99_ratio=1.0,
            query_p99_floor_ms=1.0,
        )
        self.assertFalse(passed, "1x of a 4ms quiet p99 fails a 12.8ms rolling one")
        passed, _ = evaluate(
            AT_REST_WORST, ROLLING_3_6X, WRITES_AFTER, 0, **LIMITS, max_query_p99_ratio=100.0
        )
        self.assertTrue(passed)


class TestUserErrorsAreGated(unittest.TestCase):
    """
    What escaped here: across three A/B rounds the candidate logged 107
    client-visible 4xx against the baseline's 39 -- 67 vs 24 on rolling legs, 40
    vs 15 on single-pod legs -- consistently, on both access strategies, while
    server_errors stayed at 0.00% on both sides. Nothing counted them, so all 18
    legs passed.
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
    requests_total is a gauge that is only ever Inc()d, so it resets with the
    process just like the deadline counter -- and the 4xx worth catching are the
    ones a pod answers while an *earlier* pod is down, which is exactly what the
    reset throws away.
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
    A restarted pod reports ready with lazy shard stubs and no shards open, so the
    first query per tenant pays a shard load behind a node-wide limiter. The suite
    treated the roll as one window, so that pod averaged into four minutes of
    samples. This window is now carved out and reported; see evaluate_fresh_pod
    for why the client side cannot carry a latency threshold on it.
    """

    FRESH = [40.0] * 200 + [120.0] * 25

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
        passed, lines = evaluate_fresh_pod(Phase(QUIET, [4.0] * 10, [30_000.0] * 400), 3, 15.0, 30)
        self.assertFalse(passed)
        self.assertTrue(any("only 10 queries were served" in l for l in lines))

    def test_no_sampler_is_reported_as_unavailable(self):
        passed, lines = evaluate_fresh_pod(Phase(QUIET, []), None, 15.0, 30)
        self.assertTrue(passed, "the sampler failing is already a collection problem")
        self.assertTrue(any("fresh-pod window unavailable" in l for l in lines))

    def test_a_slow_fresh_window_is_caught_by_the_query_gate_not_this_one(self):
        """Why no latency threshold here. These windows are about a fifth of the
        roll-plus-settle window, so a tenth of them going slow is around two per
        cent of it -- inside what a p99 over it reads, and the query gate reads it
        against the leg's own quiet window rather than a number nobody measured."""
        slow = [40.0] * 200 + [3_540.0] * 25
        passed, _ = evaluate_fresh_pod(Phase(AT_REST_WORST, slow), 3, 15.0, 30)
        self.assertTrue(passed, "this gate only says the window was measured")
        rolling = [40.0] * 975 + slow
        passed, lines = evaluate(AT_REST_WORST, rolling, WRITES_AFTER, 0, **LIMITS)
        self.assertFalse(passed, "but the leg does not pass")
        self.assertTrue(any("FAILED: query p99 3540ms" in l for l in lines))


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


class TestWithinAnyWindow(unittest.TestCase):
    def test_overlapping_windows_take_a_sample_once(self):
        """Pods restart closer together than a window is long."""
        samples = [(10.0, 5.0, True), (12.0, 7.0, True)]
        served, failed = within_any(samples, [(0.0, 15.0), (8.0, 23.0)])
        self.assertEqual(served, [5.0, 7.0])
        self.assertEqual(failed, [])

    def test_the_split_keeps_failures_with_their_durations(self):
        samples = [(10.0, 5.0, True), (11.0, 30_000.0, False), (99.0, 4.0, True)]
        served, failed = within_any(samples, [(0.0, 15.0)])
        self.assertEqual(served, [5.0])
        self.assertEqual(failed, [30_000.0])

    def test_no_windows_selects_nothing(self):
        self.assertEqual(within_any([(1.0, 2.0, True)], []), ([], []))


if __name__ == "__main__":
    unittest.main()
