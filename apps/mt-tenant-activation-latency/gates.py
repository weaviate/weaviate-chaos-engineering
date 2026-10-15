"""
Gates for the tenant-activation-during-rollout test.

Kept free of the weaviate and k8s clients so the thresholds can be tested on
recorded numbers. See test_gates.py.

Why this test exists: reconciling tenant status and activating a tenant are both
applied from the replicated log, and that apply is serial. Anything expensive done
there delays every entry behind it, so a request needing a recent schema version
waits. The restart QPS benchmark cannot see it because it drives a single tenant
per collection, leaving nothing for either path to walk.

What to gate on, learned the hard way: the client reaches the cluster through a
port-forward, and a rolling restart breaks that connection. A single query can
therefore sit for 15s on a perfectly healthy build. Gating on the slowest sample
made the verdict a coin toss on reconnect timing -- it failed one post-fix leg at
15,015ms and passed another at 3,014ms, both artifacts. Percentiles over hundreds
of samples are not moved by one reconnect, and they separate the builds by orders
of magnitude, so they gate and the extremes are reported.

The same reconnect is why the one measurement taken from the server rather than
the client -- the 4xx in evaluate_user_errors -- can be gated far more tightly
than anything here. A lost port-forward produces no HTTP answer at all, so it
cannot move a counter that only advances when a handler answered.

And it is why the query p99 limit is relative. A fixed ceiling has to clear the
noisiest leg, which in this environment leaves 7-20x of headroom over what the
suite measures at rest -- more than the degradation that escaped it. Bounding the
roll against the leg's own quiet window asks the question the product asks ("the
customer should not see that a rollout happened") and asks it the same way in an
environment that is ten times slower at rest. See evaluate.
"""

import math
import os
import re
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

WAIT_METRIC = "weaviate_cluster_store_wait_for_index_duration_seconds"
_START_TIME = re.compile(r"^process_start_time_seconds\s+([0-9.e+]+)$", re.M)
# RequestStatus.String() in adapters/handlers/rest/requests_total_metrics.go.
# There is no "failed": a gate written against that label can never fire.
REQUESTS_METRIC = "requests_total"
USER_ERROR_STATUS = "user_error"
_REQUESTS = re.compile(r"^requests_total\{([^}]*)\}\s+([0-9.e+]+)$")
_LABELS = re.compile(r'(\w+)="([^"]*)"')


def get_env_int(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    return int(v) if v else default


def get_env_float(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    return float(v) if v else default


def _start_time(text: str) -> Optional[float]:
    m = _START_TIME.search(text)
    return float(m.group(1)) if m else None


def _deadline_count(text: str) -> Tuple[int, bool]:
    """(deadline count, whether this build exposes the metric at all)."""
    total, exposed = 0, False
    for line in text.splitlines():
        if not line.startswith(WAIT_METRIC):
            continue
        exposed = True
        if line.startswith(WAIT_METRIC + "_count") and 'outcome="deadline"' in line:
            total += int(float(line.rsplit(" ", 1)[1]))
    return total, exposed


def _user_error_count(text: str) -> Tuple[int, bool]:
    """(4xx answered, whether this build exposes the metric at all).

    requests_total is a gauge that is only ever Inc()d, so within one process it
    reads as a counter and it resets with the process -- the same shape as the
    deadline counter, and the same reason the window has to be sampled.

    The labels are parsed rather than looked for as a substring: the series
    carries status, class_name, api and query_type, and the order prometheus
    renders them in is not part of any contract.
    """
    total, exposed = 0, False
    for line in text.splitlines():
        m = _REQUESTS.match(line.strip())
        if not m:
            continue
        exposed = True
        if dict(_LABELS.findall(m.group(1))).get("status") == USER_ERROR_STATUS:
            total += int(float(m.group(2)))
    return total, exposed


class _Reading(NamedTuple):
    """One pod's counter, with the process identity needed to read it."""

    start_time: Optional[float]
    count: int
    exposed: bool


def _read_deadlines(text: str) -> _Reading:
    count, exposed = _deadline_count(text)
    return _Reading(_start_time(text), count, exposed)


def _read_user_errors(text: str) -> _Reading:
    count, exposed = _user_error_count(text)
    return _Reading(_start_time(text), count, exposed)


class _CounterAccumulator:
    """
    One counter accumulated per pod across restarts, by sampling the window.

    Two scrapes cannot describe a rolling restart: these counters reset with the process,
    so differencing a before against an after keeps only what each pod served after its
    *own* restart -- and what these gates catch is what a pod did while an *earlier* pod
    was down. A counter that climbs 0 to 7 and resets reports as zero.

    Sampling keeps them: each observation closes a delta against that pod's previous
    sample, so the loss is bounded by the sampling interval. Same shape as
    server_metrics.WindowAccumulator. A pod missing from an observation is not advanced and
    resumes on its next appearance; only the baseline and the final scrape must be complete.
    """

    def __init__(self, baseline: Dict[str, str], read: Callable[[str], _Reading]) -> None:
        self._read = read
        self._last: Dict[str, _Reading] = {pod: read(text) for pod, text in baseline.items()}
        self._exposed = any(r.exposed for r in self._last.values())
        self._total = 0

    def observe(self, texts: Dict[str, str]) -> None:
        """Fold one scrape of every reachable pod into the window."""
        for pod, text in texts.items():
            now = self._read(text)
            self._exposed = self._exposed or now.exposed
            was = self._last.get(pod)
            restarted = (
                was is not None
                and was.start_time is not None
                and now.start_time is not None
                and was.start_time != now.start_time
            )
            if was is None or restarted:
                # new, or restarted since its last sample: its counters are the
                # window, and nothing is subtracted from them
                self._total += now.count
            else:
                # A scrape without process_start_time_seconds is partial, not a restart:
                # reading it as one would add the pod's whole lifetime counter.
                self._total += max(0, now.count - was.count)
            if now.start_time is None and was is not None:
                # Keep the last one seen, or the next scrape has nothing to compare against.
                now = now._replace(start_time=was.start_time)
            self._last[pod] = now

    def total(self) -> Optional[int]:
        """The counter over the window, or None when no pod exposed the metric."""
        return self._total if self._exposed else None


class DeadlineAccumulator(_CounterAccumulator):
    """
    Schema-version waits that ran out of time, over a window that includes a roll.

    A healthy cluster never observes the deadline outcome, so that child series is
    simply absent; keying "exposed" on it would report a clean run as
    unmeasurable, which is why it keys on the parent metric instead. See
    _CounterAccumulator for why the window is sampled rather than differenced.
    """

    def __init__(self, baseline: Dict[str, str]) -> None:
        super().__init__(baseline, _read_deadlines)


class UserErrorAccumulator(_CounterAccumulator):
    """
    Client-visible 4xx the cluster answered with, over a window that includes a roll.

    Read from the server on purpose: a client cannot tell a 4xx from its port-forward
    dropping, which is why every client-side failure gate here has to be loose.
    requests_total only moves when a request reached a handler and was answered, so a
    reconnect never touches it and this gate can be tight. Same scrape and window as the
    deadline counter; see _CounterAccumulator.

    Zero is a real reading here, not an absent one, so "exposed" keys on the metric rather
    than on the user_error child series.
    """

    def __init__(self, baseline: Dict[str, str]) -> None:
        super().__init__(baseline, _read_user_errors)


def count_deadline_waits(before: Dict[str, str], after: Dict[str, str]) -> Optional[int]:
    """
    Schema-version waits that ran out of time between two scrapes.

    Sound only when at most one pod restarted. Use DeadlineAccumulator for a
    rolling restart, where every pod resets and this loses whatever each one
    accrued before its own restart -- see its docstring.
    """
    accumulator = DeadlineAccumulator(before)
    accumulator.observe(after)
    return accumulator.total()


class RestartWatcher:
    """
    When each pod came back, as the metrics sampler saw it.

    process_start_time_seconds changes only when the process does, and rides in the same
    scrape as the deadline counter, so this costs no extra kubectl. It buys a timestamp,
    not an identity: the client reaches the cluster through one port-forward and does not
    know which pod answered, which is why evaluate_fresh_pod reports this window without
    putting a latency threshold on it.
    """

    def __init__(self, baseline: Dict[str, str]) -> None:
        self._start: Dict[str, Optional[float]] = {
            pod: _start_time(text) for pod, text in baseline.items()
        }
        self._came_back: List[float] = []

    def observe(self, texts: Dict[str, str], at: float) -> None:
        """Fold one scrape, taken at monotonic time `at`, into the history."""
        for pod, text in texts.items():
            now = _start_time(text)
            if now is None:
                continue
            if pod not in self._start or self._start[pod] != now:
                self._came_back.append(at)
            self._start[pod] = now

    def windows(self, length_s: float) -> List[Tuple[float, float]]:
        """The first length_s after each pod came back. May overlap."""
        return [(at, at + length_s) for at in sorted(self._came_back)]


def within_any(
    samples: Sequence[Tuple[float, float, bool]],
    windows: Sequence[Tuple[float, float]],
) -> Tuple[List[float], List[float]]:
    """What the given windows' calls cost, split by whether they were served.

    Pods restart closer together than a window is long, so the windows overlap; a
    sample in several of them is taken once. Split the same way and for the same
    reason as `within` in activation_latency: a refusal that came back instantly
    and a request that waited out its timeout are both failures, and only the
    duration tells them apart.
    """
    served, failed = [], []
    for at, ms, ok in samples:
        if any(lo <= at <= hi for lo, hi in windows):
            (served if ok else failed).append(ms)
    return served, failed


def percentile(samples: Sequence[float], q: float) -> Optional[float]:
    """
    Nearest-rank percentile. None for an empty sample.

    ceil(q*n)-1, not round(q*(n-1)): the latter interpolates a rank and rounds it
    down into the fast mass, so 149 queries at 4ms with two at 10s reports a p99
    of 4ms -- 1.3% of queries over ten seconds, read as a clean run. Nearest rank
    puts the boundary where the definition does and reports 10s.
    """
    if not samples:
        return None
    ordered = sorted(samples)
    rank = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[rank]


def summarise(samples: Sequence[float]) -> Dict[str, Optional[float]]:
    return {
        "count": float(len(samples)),
        "p50": percentile(samples, 0.50),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99),
        "max": max(samples) if samples else None,
    }


def _fmt(s: Dict[str, Optional[float]]) -> str:
    def n(v):
        return "-" if v is None else f"{v:.0f}"

    return f"n={int(s['count'])} p50={n(s['p50'])} p99={n(s['p99'])} max={n(s['max'])} (ms)"


def evaluate(
    baseline_ms: Sequence[float],
    rollout_ms: Sequence[float],
    write_ms: Sequence[float],
    deadline_waits: Optional[int],
    max_query_p99_ms: float,
    max_write_p99_ms: float,
    min_queries: int,
    min_writes: int = 0,
    max_query_p99_ratio: float = 3.0,
    query_p99_floor_ms: float = 139.0,
    max_failure_ratio: float = 0.5,
    max_stalled_calls: int = 2,
    stall_ms: float = 20_000.0,
    query_failed_ms: Sequence[float] = (),
    write_failed_ms: Sequence[float] = (),
    problems: Sequence[str] = (),
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines).

    Writes gate first: hundreds of samples a reconnect does not move. Query p99 gates
    against the lower of max_query_p99_ms and max_query_p99_ratio x this leg's own quiet
    p99, because a fixed ceiling only says "under a second" and leaves 7-20x of headroom
    against the 43-139ms measured at rest. The ceiling stays as well, or a leg whose
    quiet window was itself terrible would pass; query_p99_floor_ms keeps the ratio out
    of the noise. Extremes are reported, never gated.

    Calls that raised arrive as how long each waited, not as a count: an instant refusal
    measured nothing and must not satisfy min_queries, while a request that waited 30s
    and timed out is the stall being hunted and must not be dropped. So failures are not
    served requests, but their waits join the distribution p99 gates, and they are gated
    three ways -- min_queries/min_writes over served calls, max_failure_ratio over their
    share of attempts (wide, since the roll breaks the port-forward), and
    max_stalled_calls over failures past stall_ms, which sits above the worst reconnect
    seen on a healthy build.

    `problems` are collection failures the caller hit -- a rejected rollout, a worker
    that never finished, an incomplete scrape. Each fails the run: a number that went
    missing must never read as a pass.
    """
    q = summarise(list(rollout_ms) + list(query_failed_ms))
    b = summarise(baseline_ms)
    w = summarise(list(write_ms) + list(write_failed_ms))
    lines = [
        f"queries, quiet:   {_fmt(b)}",
        f"queries, rolling: {_fmt(q)}",
        f"writes,  overall: {_fmt(w)}",
    ]

    passed = True

    query_limit, query_why = max_query_p99_ms, f"{max_query_p99_ms:.0f}ms"
    quiet_p99 = b["p99"]
    if quiet_p99 is not None:
        relative = max(quiet_p99 * max_query_p99_ratio, query_p99_floor_ms)
        if relative < query_limit:
            query_limit = relative
            query_why = (
                f"{relative:.0f}ms -- the lower of {max_query_p99_ratio:.1f}x this"
                f" leg's own quiet p99 of {quiet_p99:.0f}ms (floored at"
                f" {query_p99_floor_ms:.0f}ms) and the {max_query_p99_ms:.0f}ms ceiling"
            )

    if len(rollout_ms) < min_queries:
        lines.append(
            f"FAILED: only {len(rollout_ms)} queries were served while the cluster"
            f" rolled, fewer than {min_queries}; nothing was measured where it matters"
        )
        passed = False

    # Writes carry the same risk as queries, from the other direction: a p99 over
    # a handful of samples is whatever those samples were, and a worker that
    # barely ran leaves a thin, fast distribution that clears any limit.
    if len(write_ms) < min_writes:
        lines.append(
            f"FAILED: only {len(write_ms)} writes were served over the whole run,"
            f" fewer than {min_writes}; too thin a distribution to gate a p99 on"
        )
        passed = False

    for label, s, limit, why in (
        ("write", w, max_write_p99_ms, f"{max_write_p99_ms:.0f}ms"),
        ("query", q, query_limit, query_why),
    ):
        if s["p99"] is None:
            lines.append(f"FAILED: no {label} samples at all")
            passed = False
        elif s["p99"] > limit:
            lines.append(
                f"FAILED: {label} p99 {s['p99']:.0f}ms over {why}."
                " Requests queued behind a backed-up apply loop is what this test"
                " exists to catch"
            )
            passed = False
        else:
            lines.append(f"PASSED: {label} p99 {s['p99']:.0f}ms, under {why}")

    for label, s, failed_ms in (("query", q, query_failed_ms), ("write", w, write_failed_ms)):
        attempted, failed = int(s["count"]), len(failed_ms)
        if not attempted or not failed:
            continue
        share = failed / attempted
        if share > max_failure_ratio:
            lines.append(
                f"FAILED: {failed} of {attempted} {label} calls failed ({share:.0%}),"
                f" over the {max_failure_ratio:.0%} a reconnect can account for."
                " A workload that is mostly erroring has not measured serving latency"
            )
            passed = False
        else:
            lines.append(
                f"  note: {failed} of {attempted} {label} calls failed ({share:.0%}),"
                " within what losing the port-forward mid-roll accounts for"
            )

        stalled = [ms for ms in failed_ms if ms >= stall_ms]
        if len(stalled) > max_stalled_calls:
            lines.append(
                f"FAILED: {len(stalled)} {label} calls waited longer than"
                f" {stall_ms:.0f}ms and were never served, more than the"
                f" {max_stalled_calls} a reconnect can account for; the slowest"
                f" waited {max(stalled):.0f}ms. A request that waits that long and"
                " gets nothing is the stall this test exists to catch, whether or"
                " not enough of them moved the p99"
            )
            passed = False
        elif stalled:
            lines.append(
                f"  note: {len(stalled)} {label} calls waited over {stall_ms:.0f}ms"
                f" before failing, the slowest {max(stalled):.0f}ms"
            )

    # Over served queries only. The note exists to excuse one slow *answer*; a
    # slow failure is the stall gate's business, and saying "not gated" about a
    # call that gate just failed on would read as a contradiction.
    served_max = max(rollout_ms) if rollout_ms else None
    if served_max is not None and served_max > max_query_p99_ms:
        lines.append(
            f"  note: slowest single query was {served_max:.0f}ms. Not gated -- a"
            " rolling restart breaks the client's port-forward, and the reconnect"
            " alone costs seconds on a healthy build"
        )

    if deadline_waits is None:
        lines.append(
            "wait-for-version outcomes unavailable on this build; client latency is"
            " the only gate for this run"
        )
    elif deadline_waits > 0:
        lines.append(
            f"FAILED: {deadline_waits} schema-version waits hit their deadline."
            " Each one blocked a request for the full consistency-wait timeout"
        )
        passed = False
    else:
        lines.append("PASSED: no schema-version wait hit its deadline")

    for problem in problems:
        lines.append(f"FAILED: {problem}")
        passed = False

    return passed, lines


def evaluate_user_errors(
    quiet: Optional[int],
    rolling: Optional[int],
    max_user_errors: int,
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines) for the 4xx the cluster answered with.

    Gated because nothing looked at these: across three A/B rounds the candidate logged
    107 against the baseline's 39, consistently, while server_errors stayed at 0.00% on
    both sides -- so the two must not be conflated. These reached a handler and were
    refused, which on this workload means a tenant the node's schema did not know yet.

    An absolute count, not a rate: those figures carry no denominator, and a rate would
    track the write worker's volume rather than the cluster. The ranges overlap (1-9 per
    leg healthy, 4-15 regressed), so the default sits at the top of the healthy range and
    fails a leg only when it answered more 4xx than any healthy leg did. The quiet window
    is reported beside it and never gates.
    """
    if rolling is None:
        return True, [
            "4xx unavailable for this run: no pod exposed requests_total, or the"
            " window could not be scraped -- which is a collection problem and"
            " fails the run on its own. Client-visible errors are not gated here"
        ]

    lines = [
        f"4xx, quiet:   {'-' if quiet is None else quiet}",
        f"4xx, rolling: {rolling}",
    ]
    if rolling > max_user_errors:
        lines.append(
            f"FAILED: the cluster answered {rolling} requests with a client-visible"
            f" 4xx while it rolled, more than {max_user_errors}. Unlike the client's"
            " own failures these cannot be a lost port-forward -- the request"
            " reached a handler and was refused, which on this workload means a"
            " tenant the node did not know about yet. The image that escaped this"
            " suite logged 107 of these against the baseline's 39 across three"
            " rounds, and nothing gated them"
        )
        return False, lines

    lines.append(
        f"PASSED: {rolling} client-visible 4xx while the cluster rolled, at most"
        f" {max_user_errors}"
    )
    return True, lines


# Tenant lifecycle p99 over 5m windows, quiet against during-and-just-after a roll:
#
#   create  0.095s -> 3.56s  (37x)
#   delete  0.49s  -> 3.32s  (6.7x)
#
# That regression passed every leg here, and not because a threshold was loose: the
# suite's only lifecycle call was the seed, which ran before the roll, so no create or
# delete stall existed to gate. evaluate_churn gates the operation that was missing.
CHURN_QUANTILE = 0.90


class Phase(NamedTuple):
    """One lifecycle operation's windows: what it cost quiet, and while rolling.

    rolling_failed_ms are the calls that raised, kept as *how long each waited*
    for the same reason as in evaluate: a create that hung for 30s and then timed
    out measured the stall, so its wait joins the distribution the gate reads,
    while it stays out of the served count a minimum is checked against.
    """

    quiet_ms: Sequence[float] = ()
    rolling_ms: Sequence[float] = ()
    rolling_failed_ms: Sequence[float] = ()


def _fmt_quantile(samples: Sequence[float], quantile: float) -> str:
    s = summarise(samples)
    gated = percentile(samples, quantile)

    def n(v):
        return "-" if v is None else f"{v:.0f}"

    return (
        f"n={len(samples)} p50={n(s['p50'])} p{int(round(quantile * 100))}={n(gated)}"
        f" p99={n(s['p99'])} max={n(s['max'])} (ms)"
    )


def evaluate_churn(
    create: Phase,
    delete: Phase,
    max_create_ms: float,
    max_delete_ms: float,
    min_churns: int,
    failures: int = 0,
    attempts: int = 0,
    max_failures: int = 3,
    quantile: float = CHURN_QUANTILE,
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines) for the tenant-churn worker.

    Create and delete gate separately, on absolute limits. Separately because they sit ~5x
    apart at baseline (95ms against 490ms), so one combined limit would have to clear the
    slower and would let the faster regress 37x -- the regression that got through.
    Absolute because the quiet window is 20s of small samples.

    A p90 rather than a p99: an iteration is four round trips, so a rolling window holds
    tens of them, and at n<100 the nearest-rank p99 is the maximum -- gating it would gate
    the slowest sample, which one port-forward reconnect can make 15s on a healthy build.
    A p90 over n>=20 discards the slowest tenth, while the measured regression stalled a
    third of its window. The p99 and max are reported beside it.

    Failures are gated here rather than by evaluate's max_failure_ratio, which allows 50%.
    """
    passed = True
    lines: List[str] = []

    for label, phase, limit in (
        ("create", create, max_create_ms),
        ("delete", delete, max_delete_ms),
    ):
        gated = list(phase.rolling_ms) + list(phase.rolling_failed_ms)
        lines.append(f"tenant {label}, quiet:   {_fmt_quantile(list(phase.quiet_ms), quantile)}")
        lines.append(f"tenant {label}, rolling: {_fmt_quantile(gated, quantile)}")

        if len(phase.rolling_ms) < min_churns:
            lines.append(
                f"FAILED: only {len(phase.rolling_ms)} tenant {label}s were served"
                f" while the cluster rolled, fewer than {min_churns}; too thin a"
                " distribution to gate a percentile on, and a tenant lifecycle that"
                " is never exercised during the roll is what this worker exists to fix"
            )
            passed = False

        at = percentile(gated, quantile)
        pct = int(round(quantile * 100))
        if at is None:
            lines.append(f"FAILED: no tenant {label} samples at all")
            passed = False
        elif at > limit:
            lines.append(
                f"FAILED: tenant {label} p{pct} {at:.0f}ms over {limit:.0f}ms."
                " Creating or deleting a tenant goes through the serial apply, so a"
                " rollout that backs that apply up stalls it -- measured at 3.56s"
                " (create) and 3.32s (delete) on a build this suite otherwise passed"
            )
            passed = False
        else:
            lines.append(f"PASSED: tenant {label} p{pct} {at:.0f}ms, under {limit:.0f}ms")

    if failures > max_failures:
        lines.append(
            f"FAILED: {failures} of {attempts} tenant-churn iterations could not"
            f" complete, more than {max_failures}. Not folded into the query and"
            " write failure ratio on purpose: that allowance is 50%, which a build"
            " failing every second tenant create would clear"
        )
        passed = False
    elif failures:
        lines.append(
            f"  note: {failures} of {attempts} tenant-churn iterations could not"
            " complete, within what losing the port-forward mid-roll accounts for"
        )

    return passed, lines


# A p90 for the same reason evaluate_churn uses one: the roll breaks the client's
# port-forward and one reconnect cost 15,015ms on a build known to be fine, and a
# percentile only shrugs that off with enough samples under it. The p50, p99 and
# max are printed beside it so a reader sees the whole shape of the window.
FRESH_QUANTILE = 0.90


def evaluate_fresh_pod(
    queries: Phase,
    windows: Optional[int],
    window_s: float,
    min_samples: int,
    quantile: float = FRESH_QUANTILE,
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines) for the first window_s after each pod came back.

    A restarted pod reports ready with lazy shard stubs, so the first query per tenant
    pays a shard load -- which the suite otherwise averages into a window minutes long.

    The latency in it is reported, not gated, because a gate that cannot fail is worse
    than none: these windows are about a fifth of the rolling one, so anything an absolute
    limit would catch evaluate's query p99 catches already; the client reaches the cluster
    through one port-forward and cannot say which pod answered, so every percentile here is
    diluted by an unknown factor; and the window's start trails readiness by up to one
    scrape interval. What is gated is that the window exists and holds samples, since a
    number that went missing must never read as a pass.
    """
    pct = int(round(quantile * 100))
    if windows is None:
        return True, [
            "fresh-pod window unavailable: no metrics sampler ran, so no pod's"
            " restart could be timed"
        ]

    gated = list(queries.rolling_ms) + list(queries.rolling_failed_ms)
    lines = [
        f"fresh pod, at rest:          {_fmt_quantile(list(queries.quiet_ms), quantile)}",
        f"fresh pod, {window_s:.0f}s after ready: {_fmt_quantile(gated, quantile)}"
        f"  [{windows} pod(s) came back]",
    ]

    passed = True
    if windows == 0:
        lines.append(
            "FAILED: the cluster rolled but no pod was observed coming back, so"
            " the first seconds after a pod reports ready were never measured."
            " Every pod's process restarts in a rolling restart, so seeing none"
            " of them means the scrape was broken, not that the cluster was still"
        )
        passed = False
    elif len(queries.rolling_ms) < min_samples:
        lines.append(
            f"FAILED: only {len(queries.rolling_ms)} queries were served in the"
            f" {windows} window(s) just after a pod reported ready, fewer than"
            f" {min_samples}; the window a fresh pod is in is exactly where this"
            " workload stalls, and it went unmeasured"
        )
        passed = False
    else:
        lines.append(
            f"PASSED: {len(queries.rolling_ms)} queries measured in the"
            f" {windows} window(s) just after a pod reported ready, p{pct}"
            f" {percentile(gated, quantile):.0f}ms. Reported, not gated on"
            " latency -- see evaluate_fresh_pod"
        )

    return passed, lines
