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

import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

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


def count_deadline_waits(before: Dict[str, str], after: Dict[str, str]) -> Optional[int]:
    """
    Schema-version waits that ran out of time during the window.

    Per pod, because these counters reset when a pod restarts -- which is exactly
    what the window under test does. A pod whose process start time changed is
    counted from zero, so waits it served before restarting are not subtracted
    away; otherwise the delta is taken.

    None only when no pod exposes the metric at all. A healthy cluster never
    observes the deadline outcome, so that child series is simply absent, and
    keying "exposed" on it would report a clean run as unmeasurable.
    """
    total, exposed = 0, False
    for pod, after_text in after.items():
        count, pod_exposed = _deadline_count(after_text)
        exposed = exposed or pod_exposed
        before_text = before.get(pod)
        if before_text is None or _start_time(before_text) != _start_time(after_text):
            total += count  # new or restarted: its counters are the window
        else:
            was, _ = _deadline_count(before_text)
            total += max(0, count - was)
    return total if exposed else None


def percentile(samples: Sequence[float], q: float) -> Optional[float]:
    """Nearest-rank percentile. None for an empty sample."""
    if not samples:
        return None
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
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
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines).

    Writes gate first: they are the broadest signal, hundreds of samples that a
    reconnect does not move, and they separated the builds 8-45x on p99. Query p99
    gates too, separating ~900x. The quiet window and the extremes are reported for
    context and never gate.
    """
    q = summarise(rollout_ms)
    b = summarise(baseline_ms)
    w = summarise(write_ms)
    lines = [
        f"queries, quiet:   {_fmt(b)}",
        f"queries, rolling: {_fmt(q)}",
        f"writes,  overall: {_fmt(w)}",
    ]

    passed = True
    if q["count"] < min_queries:
        lines.append(
            f"FAILED: only {int(q['count'])} queries ran while the cluster rolled,"
            f" fewer than {min_queries}; nothing was measured where it matters"
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

    if q["max"] is not None and q["max"] > max_query_p99_ms:
        lines.append(
            f"  note: slowest single query was {q['max']:.0f}ms. Not gated -- a"
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

    return passed, lines
