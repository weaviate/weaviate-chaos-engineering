"""
Read query latency and error rate from Weaviate's own metrics.

Copied from the restart-highly-available-qps-benchmark app, where it is unit
tested; the apps are otherwise self-contained.

Why server-side: the benchmark client reports a p99 per one-second row, and at
20 QPS a row covers ~20 queries, so that "p99" is really a maximum and swings
between runs. queries_durations_ms_bucket is a histogram over every query the
server answered, which is what made the regression unmissable in production.

The histogram is observed in one place, Traverser.GetClass, which both the REST
GraphQL handler and the gRPC search service call, so hybrid search over the gRPC
client does land in it -- the query_type label reads "get_graphql" either way
because it is hardcoded there. Aggregates go through Traverser.Aggregate, which
only moves a gauge, so they are not in the histogram and have to be timed by the
caller.

requests_total is narrower: only the REST and GraphQL handlers emit it, so the
error rate covers the aggregate driver's GraphQL calls but not the benchmark's
gRPC hybrid searches, which appear in neither numerator nor denominator. A gRPC
search that fails is still caught by the latency histogram and the client's
timeout count, not by this rate.

Snapshots are kept per pod. A restarted pod's counters go back to zero, and
summing pods before differencing would let a pod that did not restart contribute
its lifetime history to the window: before=3000 over three pods, two restart,
after=50+50+1100=1200, and a single summed delta reads 1200 as window traffic
when only 200 of it is. Per-pod deltas are summed only once each is reset-aware.

Free of the weaviate and k8s client libraries so it can be tested on recorded
metrics text. See test_server_metrics.py.
"""

import re
from typing import Dict, List, NamedTuple, Optional, Tuple

BUCKET_RE = re.compile(r"^queries_durations_ms_bucket\{(?P<labels>[^}]*)\}\s+(?P<value>[0-9.e+]+)$")
REQUESTS_RE = re.compile(r"^requests_total\{(?P<labels>[^}]*)\}\s+(?P<value>[0-9.e+]+)$")
# Registered by default with client_golang's default registry, which promauto
# writes to, so every pod exposes it. It changes only when the process does.
START_TIME_RE = re.compile(r"^process_start_time_seconds\s+(?P<value>[0-9.e+]+)$")
LABEL_RE = re.compile(r'(\w+)="([^"]*)"')

# RequestStatus.String() in adapters/handlers/rest/requests_total_metrics.go.
# There is no "failed": a gate written against that label can never fire.
STATUS_OK = "ok"
STATUS_USER_ERROR = "user_error"
STATUS_SERVER_ERROR = "server_error"


class PodSnapshot(NamedTuple):
    """One pod's counters, with the process identity needed to read them."""

    buckets: Dict[float, float]
    requests: Dict[str, float]
    start_time: Optional[float]


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
    """requests_total summed by status, e.g. {"ok": 1234, "server_error": 7}."""
    out: Dict[str, float] = {}
    for line in metrics_text.splitlines():
        m = REQUESTS_RE.match(line.strip())
        if not m:
            continue
        status = _labels(m.group("labels")).get("status", "unknown")
        out[status] = out.get(status, 0.0) + float(m.group("value"))
    return out


def parse_start_time(metrics_text: str) -> Optional[float]:
    """The pod's process start time, or None if it is not exposed."""
    for line in metrics_text.splitlines():
        m = START_TIME_RE.match(line.strip())
        if m:
            return float(m.group("value"))
    return None


def snapshot_pods(texts: Dict[str, str], query_type: str = "get_graphql") -> Dict[str, PodSnapshot]:
    """Parse each pod's metrics text separately, keyed by pod name."""
    return {
        pod: PodSnapshot(
            parse_latency_buckets(text, query_type),
            parse_request_counts(text),
            parse_start_time(text),
        )
        for pod, text in texts.items()
    }


def bucket_delta(before: Dict[float, float], after: Dict[float, float]) -> Dict[float, float]:
    """
    Counts accrued between two snapshots of one pod.

    Used when the pod's process identity is unknown. A bound that went backwards
    is taken as-is rather than as a negative delta, so a restart cannot subtract
    from the window. It cannot see a reset followed by more traffic than the
    baseline -- that needs parse_start_time.
    """
    delta: Dict[float, float] = {}
    for bound, later in after.items():
        earlier = before.get(bound, 0.0)
        delta[bound] = later - earlier if later >= earlier else later
    return delta


def counter_delta(before: Dict[str, float], after: Dict[str, float]) -> Dict[str, float]:
    """bucket_delta's reset rule, for the request counters of one pod."""
    delta: Dict[str, float] = {}
    for status, later in after.items():
        earlier = before.get(status, 0.0)
        delta[status] = later - earlier if later >= earlier else later
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
    before: Dict[str, PodSnapshot],
    after: Dict[str, PodSnapshot],
) -> Dict[str, Optional[float]]:
    """
    Latency quantiles and error rate for the interval between two snapshots.

    A pod whose process start time changed restarted during the window, so its
    counters already *are* the window and nothing is subtracted. That is exact
    where the backwards-delta rule is only a guess: a pod that reset and then
    served more than the baseline looks like ordinary growth, and subtracting the
    baseline would understate it. Pods that do not expose a start time fall back
    to the heuristic.

    A pod only in `after` came up during the window, so its counters are the
    window. A pod only in `before` could not be scraped at the end; its traffic
    is unrecoverable and reported as missing rather than guessed at.

    error_rate counts server_error only, over REST/GraphQL requests (see the
    module docstring for why gRPC is absent). user_error is a rejected request, not a
    broken cluster, so folding it in would let a malformed query fail a rollout;
    it is returned separately so a run can still show it.
    """
    buckets: Dict[float, float] = {}
    statuses: Dict[str, float] = {}

    restarts = 0
    for pod, snap in after.items():
        prev = before.get(pod)
        restarted = prev is None or (
            snap.start_time is not None
            and prev.start_time is not None
            and snap.start_time != prev.start_time
        )
        if restarted:
            restarts += 1
            pod_buckets, pod_requests = snap.buckets, snap.requests
        else:
            pod_buckets = bucket_delta(prev.buckets, snap.buckets)
            pod_requests = counter_delta(prev.requests, snap.requests)
        for bound, count in pod_buckets.items():
            buckets[bound] = buckets.get(bound, 0.0) + count
        for status, count in pod_requests.items():
            statuses[status] = statuses.get(status, 0.0) + count

    server_errors = max(statuses.get(STATUS_SERVER_ERROR, 0.0), 0.0)
    user_errors = max(statuses.get(STATUS_USER_ERROR, 0.0), 0.0)
    ok = max(statuses.get(STATUS_OK, 0.0), 0.0)
    total = ok + user_errors + server_errors

    return {
        "queries": max(buckets.values()) if buckets else 0.0,
        "p50_ms": quantile_from_buckets(buckets, 0.50),
        "p99_ms": quantile_from_buckets(buckets, 0.99),
        "server_errors": server_errors,
        "user_errors": user_errors,
        "requests": total,
        "error_rate": (server_errors / total) if total else 0.0,
        "pods": float(len(after)),
        "pods_restarted": float(restarts),
        "pods_missing": float(len(set(before) - set(after))),
    }
