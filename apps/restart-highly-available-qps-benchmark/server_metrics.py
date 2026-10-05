"""
Read query latency and error rate from Weaviate's own metrics.

Why server-side: the benchmark client reports a p99 per one-second row, and at
20 QPS a row covers ~20 queries, so that "p99" is really a maximum and swings
between runs. queries_durations_ms_bucket is a histogram over every query the
server answered, which is what made the regression unmissable in production.

Only query_type="get_graphql" populates it -- gRPC queries are not instrumented
-- so the probe that drives it has to speak GraphQL.

Free of the weaviate and k8s client libraries so it can be tested on recorded
metrics text. See test_server_metrics.py.
"""

import re
from typing import Dict, List, Optional, Tuple

BUCKET_RE = re.compile(r"^queries_durations_ms_bucket\{(?P<labels>[^}]*)\}\s+(?P<value>[0-9.e+]+)$")
REQUESTS_RE = re.compile(r"^requests_total\{(?P<labels>[^}]*)\}\s+(?P<value>[0-9.e+]+)$")
LABEL_RE = re.compile(r'(\w+)="([^"]*)"')


def _labels(raw: str) -> Dict[str, str]:
    return dict(LABEL_RE.findall(raw))


def parse_latency_buckets(metrics_text: str, query_type: str = "get_graphql") -> Dict[float, float]:
    """
    Cumulative query-latency buckets, summed over classes and shards.

    Returns {upper_bound_ms: cumulative_count}. Prometheus buckets are
    cumulative, so counts are summed per bound across every series.
    """
    buckets: Dict[float, float] = {}
    for line in metrics_text.splitlines():
        m = BUCKET_RE.match(line.strip())
        if not m:
            continue
        labels = _labels(m.group("labels"))
        if query_type and labels.get("query_type") != query_type:
            continue
        le = labels.get("le")
        if le is None:
            continue
        bound = float("inf") if le == "+Inf" else float(le)
        buckets[bound] = buckets.get(bound, 0.0) + float(m.group("value"))
    return buckets


def parse_request_counts(metrics_text: str) -> Dict[str, float]:
    """requests_total summed by status, e.g. {"ok": 1234, "failed": 7}."""
    out: Dict[str, float] = {}
    for line in metrics_text.splitlines():
        m = REQUESTS_RE.match(line.strip())
        if not m:
            continue
        status = _labels(m.group("labels")).get("status", "unknown")
        out[status] = out.get(status, 0.0) + float(m.group("value"))
    return out


def bucket_delta(before: Dict[float, float], after: Dict[float, float]) -> Dict[float, float]:
    """
    Counts accrued between two snapshots.

    A restarted pod's counters reset to zero, so a bound that went backwards is
    taken as-is rather than as a negative delta -- otherwise a restart would
    silently subtract from the window being measured.
    """
    delta: Dict[float, float] = {}
    for bound, later in after.items():
        earlier = before.get(bound, 0.0)
        delta[bound] = later - earlier if later >= earlier else later
    return delta


def quantile_from_buckets(buckets: Dict[float, float], quantile: float) -> Optional[float]:
    """
    Interpolate a quantile from cumulative histogram buckets.

    Returns None when the window holds no queries. The value is capped at the
    largest finite bound: anything in +Inf is only known to exceed it, so
    reporting that bound understates rather than inventing a number.
    """
    if not buckets:
        return None
    ordered: List[Tuple[float, float]] = sorted(buckets.items())
    total = max(v for _, v in ordered)
    if total <= 0:
        return None

    target = total * quantile
    finite = [b for b, _ in ordered if b != float("inf")]
    if not finite:
        return None
    prev_bound, prev_count = 0.0, 0.0
    for bound, count in ordered:
        if count < target:
            prev_bound, prev_count = bound, count
            continue
        if bound == float("inf"):
            return max(finite)
        span = count - prev_count
        if span <= 0:
            return bound
        # linear interpolation within the bucket, as prometheus does
        return prev_bound + (bound - prev_bound) * (target - prev_count) / span
    return max(finite)


def summarise_window(
    before_buckets: Dict[float, float],
    after_buckets: Dict[float, float],
    before_requests: Dict[str, float],
    after_requests: Dict[str, float],
) -> Dict[str, Optional[float]]:
    """Latency quantiles and error rate for the interval between two snapshots."""
    delta = bucket_delta(before_buckets, after_buckets)
    queries = max(delta.values()) if delta else 0.0

    failed = after_requests.get("failed", 0.0) - before_requests.get("failed", 0.0)
    ok = after_requests.get("ok", 0.0) - before_requests.get("ok", 0.0)
    failed = max(failed, 0.0)
    ok = max(ok, 0.0)
    total = failed + ok

    return {
        "queries": queries,
        "p50_ms": quantile_from_buckets(delta, 0.50),
        "p99_ms": quantile_from_buckets(delta, 0.99),
        "failed_requests": failed,
        "error_rate": (failed / total) if total else 0.0,
    }
