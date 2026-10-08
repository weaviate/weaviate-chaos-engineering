"""
Gates for the tenant-activation latency test.

Kept free of the weaviate and k8s clients so the thresholds can be tested on
recorded numbers. See test_gates.py.

Why this test exists: reconciling tenant status and activating a tenant are both
applied from the replicated log, and that apply is serial. Anything expensive done
there delays every entry behind it, so a request needing a recent schema version
waits. The restart QPS benchmark cannot see it because it drives a single tenant
per collection, leaving nothing for either path to walk.
"""

import os
from typing import Dict, List, Optional, Sequence, Tuple


def get_env_int(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    return int(v) if v else default


def get_env_float(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    return float(v) if v else default


WAIT_METRIC = "weaviate_cluster_store_wait_for_index_duration_seconds"


def count_deadline_waits(texts: Dict[str, str]) -> Optional[int]:
    """
    Schema-version waits that ran out of time, summed over pods' metrics text.

    None only when the build exposes no such metric at all. A healthy cluster
    never observes the deadline outcome, so that child series is simply absent --
    keying "exposed" on it would report a clean run as unmeasurable. Presence is
    decided from any outcome, since `immediate` is observed constantly.
    """
    total, exposed = 0, False
    for text in texts.values():
        for line in text.splitlines():
            if not line.startswith(WAIT_METRIC):
                continue
            exposed = True
            if line.startswith(WAIT_METRIC + "_count") and 'outcome="deadline"' in line:
                total += int(float(line.rsplit(" ", 1)[1]))
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


def evaluate(
    baseline_ms: Sequence[float],
    rollout_ms: Sequence[float],
    write_ms: Sequence[float],
    deadline_waits: Optional[int],
    max_query_ms: float,
    min_queries: int,
    server: Optional[Dict[str, Optional[float]]] = None,
) -> Tuple[bool, List[str]]:
    """
    Returns (passed, report lines).

    Gates on the queries issued while the cluster is rolling, with the quiet
    window reported beside them so a run shows how much the rollout cost rather
    than just an absolute number. The client is what separated the two builds:
    50.2s before the fix against 1.35s after.

    deadline_waits counts the pathology directly and gates too, but only when the
    build exposes the metric, otherwise an older image would pass by not
    reporting. The server's own query histogram is reported and NOT gated: on
    these same builds it read 262-360ms against 263-432ms and did not separate
    them, because a query stalled on a schema version is usually refused rather
    than recorded as slow.
    """
    q = summarise(rollout_ms)
    b = summarise(baseline_ms)
    w = summarise(write_ms)
    lines = [
        f"queries, quiet:   n={int(b['count'])} p50={b['p50']} p99={b['p99']} max={b['max']} (ms)",
        f"queries, rolling: n={int(q['count'])} p50={q['p50']} p99={q['p99']} max={q['max']} (ms)",
        f"writes,  overall: n={int(w['count'])} p50={w['p50']} p99={w['p99']} max={w['max']} (ms)",
    ]

    passed = True
    if q["count"] < min_queries:
        lines.append(
            f"FAILED: only {int(q['count'])} queries ran while the cluster rolled,"
            f" fewer than {min_queries}; nothing was measured where it matters"
        )
        passed = False
    elif q["max"] is not None and q["max"] > max_query_ms:
        lines.append(
            f"FAILED: slowest query while rolling {q['max']:.0f}ms over"
            f" {max_query_ms:.0f}ms. A query waiting on a schema version that a"
            " backed-up apply loop has not reached is what this test exists to catch"
        )
        passed = False
    else:
        lines.append(
            f"PASSED: slowest query while rolling {q['max']:.0f}ms," f" under {max_query_ms:.0f}ms"
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
