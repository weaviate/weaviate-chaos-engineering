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
15,015ms and passed another at 3,014ms, both artifacts.

Percentiles came next and flaked the same way, one notch down. A p99 over ~1,200
calls is the twelfth slowest, which is a dozen reconnects, GC pauses and scheduler
hiccups, and a limit tuned to clear healthy runs sits inside that noise: the
write p99 ran 458-663ms across one week's healthy legs against a limit moved from
500 to 600ms, and the query p99's limit was 3x a quiet p99 that itself swung
17-69ms from one run to the next. Seven of twelve legs failed, each by about 10%.

The regression this suite exists to catch does not look like that. A backed-up
apply loop holds *every* request that needs a schema version, for as long as it
is backed up -- seconds to minutes. So the gate asks for that shape and nothing
else: over every window_s stretch of the roll, the median of what the calls in it
waited. A median only moves when more than half of a window's calls were slow,
which one reconnect, one slow answer or a scattered tail cannot do, while a stall
does so in every window it covers. The median of the leg's own quiet window sets
the limit (times a ratio, with a floor so a ratio between single-digit
milliseconds is not a verdict, and a ceiling so a terrible quiet window cannot
buy headroom), which asks the question the product asks -- the customer should
not see that a rollout happened -- and asks it the same way on a runner that is
ten times slower at rest. See Limit, sliding_windows and evaluate_sustained.

The p99 and max are still printed beside every window so the shape of a run can
be read from its log; they no longer decide it. What does, besides the windows:
counts and shares that one artifact cannot produce -- calls that waited past
stall_ms and were never served, the share of calls refused, the schema-version
waits the server itself timed out, and the 4xx it answered. That last one can be
gated tightly for the same reason the client-side gates cannot: a lost
port-forward produces no HTTP answer, so it never moves a server-side counter.
"""

import bisect
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


# (monotonic seconds the call began, milliseconds it waited, whether the cluster served
# it). The wait is kept whether or not the call was served: a refusal that came back
# instantly and a request that waited out its timeout are both failures, and only the
# duration tells them apart.
Sample = Tuple[float, float, bool]


def served_ms(samples: Sequence[Sample]) -> List[float]:
    return [ms for _, ms, ok in samples if ok]


def failed_ms(samples: Sequence[Sample]) -> List[float]:
    return [ms for _, ms, ok in samples if not ok]


def waits_ms(samples: Sequence[Sample]) -> List[float]:
    """Every call's wait, served or not."""
    return [ms for _, ms, _ in samples]


def within(samples: Sequence[Sample], lo: float, hi: float) -> List[Sample]:
    """The calls that began inside [lo, hi], in the order they began."""
    return [s for s in samples if lo <= s[0] <= hi]


def within_any(samples: Sequence[Sample], windows: Sequence[Tuple[float, float]]) -> List[Sample]:
    """The calls that began inside any of the windows, each taken once.

    Pods restart closer together than a window is long, so the windows overlap; a
    sample in several of them is taken once.
    """
    return [s for s in samples if any(lo <= s[0] <= hi for lo, hi in windows)]


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


class SlowWindow(NamedTuple):
    """One window_s stretch of the roll, as the median wait of every call that began in it."""

    start: float
    end: float
    median_ms: float
    calls: int


def sliding_windows(
    samples: Sequence[Sample], window_s: float, min_samples: int
) -> List[SlowWindow]:
    """
    Every window_s stretch that begins on a call and holds at least min_samples calls.

    Anchored on the calls rather than on a fixed grid, so a stall cannot be split
    across two windows that each look fine. The median is over every call that began
    in the window, served or not: a request that waited out its timeout measured the
    stall, and an instant refusal during a reconnect only pulls the median down,
    which errs towards passing and is what the failure-share gate is for.

    The worker is serial, so a slow cluster produces *fewer* calls per window, not
    more. min_samples is therefore the floor on what can be judged, and window_s has
    to be long enough that a stall still fills it: a request held behind the apply
    loop waits up to RAFT_CONSISTENCY_WAIT_TIMEOUT (10s by default) before it is
    served or counted as a deadline, so 60s of such calls is six of them. The same
    two numbers are what keep a reconnect out: a 15s hang is one call, the rollout
    cannot restart pods fast enough to put three of them in one minute, and two are
    never the majority of five. A window too thin to judge is skipped, never passed
    -- see evaluate_sustained for what happens when no window qualifies at all.

    Only windows that lie wholly inside the measured span are judged. One that runs
    past the last call holds only the few calls at the end, and those are whatever
    the end happened to be -- two hangs after a quiet minute read as a stall.
    """
    ordered = sorted(samples, key=lambda s: s[0])
    if not ordered:
        return []
    starts = [at for at, _, _ in ordered]
    span_end = max(at + ms / 1000.0 for at, ms, _ in ordered)
    out: List[SlowWindow] = []
    for i, (at, _, _) in enumerate(ordered):
        if at + window_s > span_end:
            break
        j = bisect.bisect_left(starts, at + window_s)
        waits = [ms for _, ms, _ in ordered[i:j]]
        if len(waits) < min_samples:
            continue
        median = percentile(waits, 0.5)
        assert median is not None
        out.append(SlowWindow(at, at + window_s, median, len(waits)))
    return out


def stretches(windows: Sequence[SlowWindow]) -> List[Tuple[float, float]]:
    """Overlapping windows merged into (start, end) spans, in time order."""
    spans: List[Tuple[float, float]] = []
    for w in sorted(windows, key=lambda w: w.start):
        if spans and w.start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], w.end))
        else:
            spans.append((w.start, w.end))
    return spans


class Limit(NamedTuple):
    """
    What a sustained median may reach: ratio x the leg's own quiet median, floored and capped.

    Relative, because a fixed limit has to clear the noisiest runner and then leaves an
    order of magnitude of headroom on the others. floor_ms keeps the ratio out of the
    noise -- 5x of a 4ms quiet median is 20ms, which a restarting replica can cost a
    healthy request -- and ceiling_ms keeps a leg whose quiet window was itself terrible
    from buying headroom with it. With no quiet window at all the ceiling is the limit:
    no reference is not a reason to stop gating.
    """

    ratio: float
    floor_ms: float
    ceiling_ms: float

    def resolve(self, quiet: Sequence[Sample]) -> Tuple[float, str]:
        """(limit in ms, how it was arrived at)."""
        quiet_median = percentile(waits_ms(quiet), 0.5)
        if quiet_median is None:
            return self.ceiling_ms, f"the {self.ceiling_ms:.0f}ms ceiling (no quiet window)"
        relative = max(quiet_median * self.ratio, self.floor_ms)
        if relative < self.ceiling_ms:
            return relative, (
                f"{relative:.0f}ms -- {self.ratio:.1f}x this leg's own quiet median of"
                f" {quiet_median:.0f}ms, floored at {self.floor_ms:.0f}ms"
            )
        return self.ceiling_ms, (
            f"the {self.ceiling_ms:.0f}ms ceiling -- {self.ratio:.1f}x the quiet median of"
            f" {quiet_median:.0f}ms would sit above it"
        )


# Queries: 4ms quiet median on these legs, 43-139ms at the p99. The floor is the top of
# that at-rest p99 band -- a median there means half of a minute's calls waited longer
# than the slowest 1% do at rest. The ceiling is the old fixed p99 limit: a median held
# there for a minute is broken whatever the quiet window did.
QUERY_LIMIT = Limit(ratio=5.0, floor_ms=139.0, ceiling_ms=1000.0)
# Writes: ~110ms quiet median. The floor is what the p99 used to be allowed, so a median
# past it needs no argument.
WRITE_LIMIT = Limit(ratio=5.0, floor_ms=500.0, ceiling_ms=2000.0)
# A window has to be long enough that a stall still fills it -- see sliding_windows.
SUSTAINED_WINDOW_S = 60.0
MIN_WINDOW_SAMPLES = 5


def evaluate_sustained(
    label: str,
    quiet: Sequence[Sample],
    rolling: Sequence[Sample],
    limit: Limit,
    window_s: float = SUSTAINED_WINDOW_S,
    min_samples: int = MIN_WINDOW_SAMPLES,
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines): did any window_s stretch of the roll spend more than
    half its calls waiting past the limit?

    No window qualifying is a failure, not a pass. The caller's minimum-sample gate has
    already established that calls were made, so a roll in which no 60s stretch held
    five of them is one where each call took most of that long -- the stall, seen from
    the other side.

    What this cannot see: a stall shorter than window_s. The worker is serial, so the
    fast calls either side of a short stall outnumber the slow ones inside it in any
    window long enough to hold both. The window is sized so that the stall that
    escaped (seconds per call, for minutes) fills it several times over, and a short
    one is what the p99 printed beside the verdict is for.
    """
    threshold, why = limit.resolve(quiet)
    judged = sliding_windows(rolling, window_s, min_samples)
    if not judged:
        return False, [
            f"FAILED: no {window_s:.0f}s stretch of the roll held {min_samples} {label}"
            f" calls, so no window could be judged against {why}. Calls that each take"
            " most of a window are a stall in themselves"
        ]
    worst = max(judged, key=lambda w: w.median_ms)
    breached = [w for w in judged if w.median_ms > threshold]
    if breached:
        spans = stretches(breached)
        longest = max(end - start for start, end in spans)
        return False, [
            f"FAILED: {label} median {worst.median_ms:.0f}ms over {why}, across a"
            f" {window_s:.0f}s window of {worst.calls} calls; {len(spans)} such stretch(es),"
            f" the longest {longest:.0f}s. Half of everything sent in that window waited"
            " that long, which no reconnect or single slow answer can produce -- requests"
            " queued behind a backed-up apply loop is what this test exists to catch"
        ]
    return True, [
        f"PASSED: {label} median under {why} in every {window_s:.0f}s window"
        f" ({len(judged)} judged, the worst {worst.median_ms:.0f}ms over {worst.calls} calls)"
    ]


def evaluate(
    quiet: Sequence[Sample],
    rolling: Sequence[Sample],
    writes_quiet: Sequence[Sample],
    writes_rolling: Sequence[Sample],
    deadline_waits: Optional[int],
    min_queries: int,
    min_writes: int = 0,
    query_limit: Limit = QUERY_LIMIT,
    write_limit: Limit = WRITE_LIMIT,
    window_s: float = SUSTAINED_WINDOW_S,
    min_window_samples: int = MIN_WINDOW_SAMPLES,
    max_failure_ratio: float = 0.5,
    max_stalled_calls: int = 2,
    stall_ms: float = 20_000.0,
    problems: Sequence[str] = (),
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines).

    Queries and writes each gate on the sustained median of their rolling window against
    their own quiet window -- see evaluate_sustained and Limit. Their p99 and max are
    reported beside it and never gate: one reconnect or one slow answer moves both on a
    healthy build, and a limit tuned to clear that noise is crossed by it about one run
    in two (see the module docstring).

    Calls that raised arrive with how long each waited, not as a count: an instant
    refusal measured nothing and must not satisfy min_queries, while a request that
    waited 30s and timed out is the stall being hunted and must not be dropped. So
    failures are not served requests, but their waits join the windows, and they are
    gated three ways -- min_queries/min_writes over served calls, max_failure_ratio
    over their share of attempts (wide, since the roll breaks the port-forward), and
    max_stalled_calls over failures past stall_ms, which sits above the worst reconnect
    seen on a healthy build.

    `problems` are collection failures the caller hit -- a rejected rollout, a worker
    that never finished, an incomplete scrape. Each fails the run: a number that went
    missing must never read as a pass.
    """
    rollout_ms, query_failed_ms = served_ms(rolling), failed_ms(rolling)
    write_ms, write_failed_ms = served_ms(writes_rolling), failed_ms(writes_rolling)
    q = summarise(waits_ms(rolling))
    b = summarise(waits_ms(quiet))
    w = summarise(waits_ms(writes_rolling))
    wb = summarise(waits_ms(writes_quiet))
    lines = [
        f"queries, quiet:   {_fmt(b)}",
        f"queries, rolling: {_fmt(q)}",
        f"writes,  quiet:   {_fmt(wb)}",
        f"writes,  rolling: {_fmt(w)}",
        "  p99 and max are reported, not gated: one reconnect moves both on a healthy build",
    ]

    passed = True

    if len(rollout_ms) < min_queries:
        lines.append(
            f"FAILED: only {len(rollout_ms)} queries were served while the cluster"
            f" rolled, fewer than {min_queries}; nothing was measured where it matters"
        )
        passed = False

    # Writes carry the same risk as queries, from the other direction: a median over
    # a handful of samples is whatever those samples were, and a worker that barely
    # ran leaves a thin, fast distribution that clears any limit.
    if len(write_ms) < min_writes:
        lines.append(
            f"FAILED: only {len(write_ms)} writes were served while the cluster rolled,"
            f" fewer than {min_writes}; too thin a distribution to gate a median on"
        )
        passed = False

    for label, before, during, limit in (
        ("write", writes_quiet, writes_rolling, write_limit),
        ("query", quiet, rolling, query_limit),
    ):
        if not during:
            lines.append(f"FAILED: no {label} samples at all")
            passed = False
            continue
        ok, sustained_lines = evaluate_sustained(
            label, before, during, limit, window_s, min_window_samples
        )
        lines.extend(sustained_lines)
        passed = passed and ok

    for label, s, failed in (("query", q, query_failed_ms), ("write", w, write_failed_ms)):
        attempted, failures = int(s["count"]), len(failed)
        if not attempted or not failures:
            continue
        share = failures / attempted
        if share > max_failure_ratio:
            lines.append(
                f"FAILED: {failures} of {attempted} {label} calls failed ({share:.0%}),"
                f" over the {max_failure_ratio:.0%} a reconnect can account for."
                " A workload that is mostly erroring has not measured serving latency"
            )
            passed = False
        else:
            lines.append(
                f"  note: {failures} of {attempted} {label} calls failed ({share:.0%}),"
                " within what losing the port-forward mid-roll accounts for"
            )

        stalled = [ms for ms in failed if ms >= stall_ms]
        if len(stalled) > max_stalled_calls:
            lines.append(
                f"FAILED: {len(stalled)} {label} calls waited longer than"
                f" {stall_ms:.0f}ms and were never served, more than the"
                f" {max_stalled_calls} a reconnect can account for; the slowest"
                f" waited {max(stalled):.0f}ms. A request that waits that long and"
                " gets nothing is the stall this test exists to catch, whether or"
                " not enough of them filled a window"
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
    if served_max is not None and served_max > query_limit.ceiling_ms:
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
#
# The p90 is what gets printed beside the gate: an iteration is four round trips, so a
# rolling window holds tens of them, and at n<100 the nearest-rank p99 is the slowest
# sample, which one port-forward reconnect can make 15s on a healthy build.
CHURN_QUANTILE = 0.90
# Separate limits because create and delete sit ~5x apart at rest (40ms against 260ms
# median), so one limit would have to clear the slower and would let the faster regress
# 37x -- the regression that got through. Floors below the old p90 limits, so a median
# past them needs no argument; the ceiling at the measured regression, which a quiet
# window must not be allowed to excuse.
#
# The window is the shared one. An iteration is four round trips, so the churn worker
# lands about one create a minute per second of iteration: sixty when healthy, eight
# at the measured regression's 7-8s iterations, which is still above the minimum. Only
# an iteration held to the consistency deadline on every trip gets under it, and that
# is what the deadline gate counts.
CREATE_LIMIT = Limit(ratio=5.0, floor_ms=500.0, ceiling_ms=3000.0)
DELETE_LIMIT = Limit(ratio=5.0, floor_ms=1000.0, ceiling_ms=3000.0)


class Phase(NamedTuple):
    """One operation's windows: the calls made while quiet, and the calls made while rolling.

    Failed calls stay in `rolling` with how long they waited, for the same reason as
    in evaluate: a create that hung for 30s and then timed out measured the stall, so
    its wait joins the windows the gate reads, while it stays out of the served count
    a minimum is checked against.
    """

    quiet: Sequence[Sample] = ()
    rolling: Sequence[Sample] = ()


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
    min_churns: int,
    failures: int = 0,
    attempts: int = 0,
    max_failures: int = 3,
    create_limit: Limit = CREATE_LIMIT,
    delete_limit: Limit = DELETE_LIMIT,
    window_s: float = SUSTAINED_WINDOW_S,
    min_window_samples: int = MIN_WINDOW_SAMPLES,
    quantile: float = CHURN_QUANTILE,
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines) for the tenant-churn worker.

    Create and delete gate separately on the sustained median of their rolling window
    against their own quiet one -- see evaluate_sustained and the limits above. The
    regression this exists for stalled a third of a five-minute window; a stall is
    contiguous, since every create and delete goes through the same serial apply, so
    the windows inside it see a median of seconds while the windows outside see none
    of it. The p90, p99 and max are printed and never gate.

    Failures are gated here rather than by evaluate's max_failure_ratio, which allows 50%.
    """
    passed = True
    lines: List[str] = []

    for label, phase, limit in (
        ("create", create, create_limit),
        ("delete", delete, delete_limit),
    ):
        served = served_ms(phase.rolling)
        lines.append(
            f"tenant {label}, quiet:   {_fmt_quantile(waits_ms(phase.quiet), quantile)}"
        )
        lines.append(
            f"tenant {label}, rolling: {_fmt_quantile(waits_ms(phase.rolling), quantile)}"
        )

        if len(served) < min_churns:
            lines.append(
                f"FAILED: only {len(served)} tenant {label}s were served"
                f" while the cluster rolled, fewer than {min_churns}; too thin a"
                " distribution to gate a median on, and a tenant lifecycle that"
                " is never exercised during the roll is what this worker exists to fix"
            )
            passed = False

        if not phase.rolling:
            lines.append(f"FAILED: no tenant {label} samples at all")
            passed = False
            continue
        ok, sustained_lines = evaluate_sustained(
            f"tenant {label}", phase.quiet, phase.rolling, limit, window_s, min_window_samples
        )
        lines.extend(sustained_lines)
        passed = passed and ok

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


# A p90 for the same reason evaluate_churn prints one: the roll breaks the client's
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
    than none: these windows are about a fifth of the rolling one, so anything a limit
    would catch evaluate's sustained query gate catches already; the client reaches the
    cluster through one port-forward and cannot say which pod answered, so every
    percentile here is diluted by an unknown factor; and the window's start trails
    readiness by up to one scrape interval. What is gated is that the window exists and
    holds samples, since a number that went missing must never read as a pass.
    """
    pct = int(round(quantile * 100))
    if windows is None:
        return True, [
            "fresh-pod window unavailable: no metrics sampler ran, so no pod's"
            " restart could be timed"
        ]

    served = served_ms(queries.rolling)
    gated = waits_ms(queries.rolling)
    lines = [
        f"fresh pod, at rest:          {_fmt_quantile(waits_ms(queries.quiet), quantile)}",
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
    elif len(served) < min_samples:
        lines.append(
            f"FAILED: only {len(served)} queries were served in the"
            f" {windows} window(s) just after a pod reported ready, fewer than"
            f" {min_samples}; the window a fresh pod is in is exactly where this"
            " workload stalls, and it went unmeasured"
        )
        passed = False
    else:
        lines.append(
            f"PASSED: {len(served)} queries measured in the"
            f" {windows} window(s) just after a pod reported ready, p{pct}"
            f" {percentile(gated, quantile):.0f}ms. Reported, not gated on"
            " latency -- see evaluate_fresh_pod"
        )

    return passed, lines
