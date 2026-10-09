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
"""

import math
import os
import re
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

WAIT_METRIC = "weaviate_cluster_store_wait_for_index_duration_seconds"
_START_TIME = re.compile(r"^process_start_time_seconds\s+([0-9.e+]+)$", re.M)


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


class _Reading(NamedTuple):
    """One pod's deadline counter, with the process identity needed to read it."""

    start_time: Optional[float]
    count: int
    exposed: bool


def _read(text: str) -> _Reading:
    count, exposed = _deadline_count(text)
    return _Reading(_start_time(text), count, exposed)


class DeadlineAccumulator:
    """
    Deadline waits accumulated per pod across restarts, by sampling the window.

    Two scrapes cannot describe a rolling restart. These counters reset with the
    process, so for each pod only what it served after its *own* restart survives
    differencing a before against an after -- and the waits this gate exists to
    catch are the ones a pod serves while an *earlier* pod is down, which are
    exactly what the reset throws away. A counter that climbs 0 to 7 and then
    resets to 0 reports as zero.

    Sampling keeps them. Each observation closes a delta against that pod's
    previous sample, so a pod that restarts has already contributed everything up
    to its last sample, and the loss is bounded by the sampling interval instead
    of being the whole pre-restart window. The same reasoning and the same shape
    as server_metrics.WindowAccumulator in the restart QPS benchmark.

    A pod missing from an observation is simply not advanced; it resumes on its
    next appearance. Only the baseline and the final scrape have to be complete,
    and the caller is responsible for making them so.
    """

    def __init__(self, baseline: Dict[str, str]) -> None:
        self._last: Dict[str, _Reading] = {pod: _read(text) for pod, text in baseline.items()}
        self._exposed = any(r.exposed for r in self._last.values())
        self._total = 0

    def observe(self, texts: Dict[str, str]) -> None:
        """Fold one scrape of every reachable pod into the window."""
        for pod, text in texts.items():
            now = _read(text)
            self._exposed = self._exposed or now.exposed
            was = self._last.get(pod)
            if was is None or was.start_time != now.start_time:
                # new, or restarted since its last sample: its counters are the
                # window, and nothing is subtracted from them
                self._total += now.count
            else:
                self._total += max(0, now.count - was.count)
            self._last[pod] = now

    def total(self) -> Optional[int]:
        """
        Deadline waits over the window, or None when no pod exposed the metric.

        A healthy cluster never observes the deadline outcome, so that child
        series is simply absent; keying "exposed" on it would report a clean run
        as unmeasurable.
        """
        return self._total if self._exposed else None


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
    max_failure_ratio: float = 0.5,
    max_stalled_calls: int = 2,
    stall_ms: float = 20_000.0,
    query_failed_ms: Sequence[float] = (),
    write_failed_ms: Sequence[float] = (),
    problems: Sequence[str] = (),
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines).

    Writes gate first: they are the broadest signal, hundreds of samples that a
    reconnect does not move, and they separated the builds 8-45x on p99. Query p99
    gates too, separating ~900x. The quiet window and the extremes are reported for
    context and never gate.

    Calls that raised come in separately, as *how long each one waited* rather
    than a count, because that duration decides which failure it was and the two
    are not comparable. An instant connection refusal measured nothing, so it
    must not count toward min_queries -- the gate would otherwise be satisfied by
    a cluster that refused everything. A request that waited 30s and then timed
    out measured exactly what this test looks for, so dropping it would hide the
    stall it proves. So failures do not count as served requests, but their waits
    do join the distribution that p99 gates, and the three failure modes are
    gated separately:

      * min_queries / min_writes, over served requests only.
      * max_failure_ratio, over their share of what was attempted. Left wide: the
        roll breaks the client's port-forward, so a handful of failures is normal
        and only a workload that is mostly erroring measured nothing.
      * max_stalled_calls, over failures that waited longer than stall_ms. The
        worst reconnect artifact measured on a healthy build was 15,015ms, so a
        default stall_ms above that cannot be tripped by one, while a request
        that waited past it and was never served is the thing being hunted.

    `problems` are collection and orchestration failures the caller hit -- a
    rollout that was rejected, a worker that never finished, a scrape that could
    not be completed. Each one fails the run. A number that went missing must
    never read as a pass: that is the whole failure mode this gate guards, and the
    gate itself is the easiest place to lose it.
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

    for label, s, limit in (
        ("write", w, max_write_p99_ms),
        ("query", q, max_query_p99_ms),
    ):
        if s["p99"] is None:
            lines.append(f"FAILED: no {label} samples at all")
            passed = False
        elif s["p99"] > limit:
            lines.append(
                f"FAILED: {label} p99 {s['p99']:.0f}ms over {limit:.0f}ms."
                " Requests queued behind a backed-up apply loop is what this test"
                " exists to catch"
            )
            passed = False
        else:
            lines.append(f"PASSED: {label} p99 {s['p99']:.0f}ms, under {limit:.0f}ms")

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
