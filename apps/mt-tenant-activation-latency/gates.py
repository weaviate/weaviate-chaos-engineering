"""
Gates for the tenant-activation latency test.

Kept free of the weaviate and k8s clients so the thresholds can be tested on
recorded numbers. See test_gates.py.

Why this test exists: activating a tenant is applied from the replicated log, and
that apply is serial. Anything expensive done there delays every entry behind it,
so a request that needs a recent schema version waits -- and the existing restart
benchmark cannot see this, because it drives a single tenant per collection and
never moves one from COLD to HOT.
"""

import os
from typing import Dict, List, Optional, Sequence, Tuple


def get_env_int(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    return int(v) if v else default


def get_env_float(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    return float(v) if v else default


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


def evaluate(
    query_ms: Sequence[float],
    activation_ms: Sequence[float],
    deadline_waits: Optional[int],
    max_query_ms: float,
    min_queries: int,
    server: Optional[Dict[str, Optional[float]]] = None,
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines).

    The client probe gates, because it is what separated the two builds: 50.2s
    before the fix against 1.35s after. deadline_waits counts the pathology
    directly and gates too, but only when the build exposes the metric, otherwise
    an older image would pass by simply not reporting.

    The server's own query histogram is reported and NOT gated. It is the signal
    the team watches, so a run should be comparable to it, but measured on the
    same two builds it did not separate them (262-360ms against 263-432ms), and
    the alarming cluster-wide figure it once showed was an artifact of taking a
    quantile across pods that restart mid-window.
    """
    q = summarise(query_ms)
    a = summarise(activation_ms)
    lines = [
        "queries during activation: "
        + f"n={int(q['count'])} p50={q['p50']} p99={q['p99']} max={q['max']} (ms)",
        "tenant activations:        "
        + f"n={int(a['count'])} p50={a['p50']} p99={a['p99']} max={a['max']} (ms)",
    ]

    passed = True
    if q["count"] < min_queries:
        lines.append(
            f"FAILED: only {int(q['count'])} queries ran, fewer than {min_queries};"
            " nothing was measured, so the run cannot judge anything"
        )
        passed = False
    elif q["max"] is not None and q["max"] > max_query_ms:
        lines.append(
            f"FAILED: slowest query {q['max']:.0f}ms over {max_query_ms:.0f}ms."
            " A query waiting on a schema version that a backed-up apply loop has"
            " not reached is what this test exists to catch"
        )
        passed = False
    else:
        lines.append(f"PASSED: slowest query {q['max']:.0f}ms, under {max_query_ms:.0f}ms")

    if deadline_waits is None:
        lines.append(
            "wait-for-version outcomes unavailable on this build; query latency is"
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

    if server:
        lines.append(
            "server-side queries (reported, not gated): "
            + f"n={server.get('queries')} p50={server.get('p50_ms')}ms "
            + f"p99={server.get('p99_ms')}ms over {server.get('pods')} pod(s)"
        )
        if server.get("pods_missing"):
            lines.append(
                f"  {server['pods_missing']} pod(s) could not be scraped at the end,"
                " so their share of the window is missing"
            )

    return passed, lines
