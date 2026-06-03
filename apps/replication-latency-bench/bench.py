"""
replication-latency-bench
==========================

Measures replication request latency on a replicated Weaviate cluster, and in
particular the impact of the weaviate-core change

    perf(replica): short-circuit local-node replica calls in-process

by driving a replicated write/read workload against the *coordinator* node and
reading the latency that weaviate already exports for it.

Why this setup exercises the optimisation
------------------------------------------
The collection is created with replication factor 3 on a 3-node cluster, so the
single shard lives on every node. The client talks only to node-1, which is
therefore always one of the shard's replicas. Before the change, node-1's own
replica leg (Pull on reads, Push on writes) still went over a loopback
HTTP/gRPC round-trip; after it, that leg is served in-process via *DB.

At consistency level ONE the effect is largest: the in-process local leg
satisfies the consistency requirement immediately, so the coordinator can ack
without waiting on any network leg.

Which metric proves it
-----------------------
The per-shard write metrics (objects_durations_ms / batch_durations_ms) measure
work *after* a request reaches a shard, so they do NOT see the coordinator-side
loopback. The request-level histograms

    grpc_server_request_duration_seconds{grpc_service,method,status}
    http_request_duration_seconds{method,route,status_code}

sit above the replica fan-out and DO capture it. We snapshot those histograms
before and after each timed phase, subtract the cumulative buckets, and derive
p50/p95/p99 for exactly this run. Client-side latency is recorded alongside as a
cross-check.

Run the script against the baseline image and the optimised image (the chaos
harness already parameterises this via WEAVIATE_VERSION) and compare the two
result files; pass COMPARE_TO=<old results.json> to print the delta inline.
"""

import json
import os
import random
import statistics
import sys
import time
import uuid as uuidlib
from typing import Dict, List, Optional, Tuple

import requests
from loguru import logger

import weaviate
from weaviate.classes.config import Configure, ConsistencyLevel, DataType, Property
from weaviate.classes.data import DataObject
from weaviate.classes.init import AdditionalConfig, Timeout
from prometheus_client.parser import text_string_to_metric_families

# ── configuration (env-driven) ───────────────────────────────────────────────


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"invalid int {name}={raw!r}; using {default}")
        return default


HTTP_HOST = os.getenv("WEAVIATE_HOST", "localhost")
HTTP_PORT = _int("WEAVIATE_HTTP_PORT", 8080)
GRPC_PORT = _int("WEAVIATE_GRPC_PORT", 50051)
METRICS_URL = os.getenv("METRICS_URL", f"http://{HTTP_HOST}:2112/metrics")

COLLECTION = os.getenv("COLLECTION", "LocalReplicaLatencyBench")
REPLICATION_FACTOR = _int("REPLICATION_FACTOR", 3)
DIM = _int("DIM", 32)
OBJECTS = _int("OBJECTS", 5000)
BATCH_SIZE = _int("BATCH_SIZE", 10)
READS = _int("READS", 5000)
# Consistency levels to benchmark, in order. ONE is the headline case.
CONSISTENCY_LEVELS = [
    c.strip().upper() for c in os.getenv("CONSISTENCY", "ONE,ALL").split(",") if c.strip()
]

RESULTS_PATH = os.getenv("RESULTS_PATH", "/workdir/results.json")
COMPARE_TO = os.getenv("COMPARE_TO", "").strip()
WEAVIATE_VERSION = os.getenv("WEAVIATE_VERSION", "unknown")
SEED = _int("SEED", 42)

_CL = {
    "ONE": ConsistencyLevel.ONE,
    "QUORUM": ConsistencyLevel.QUORUM,
    "ALL": ConsistencyLevel.ALL,
}

# Request-level histograms that sit above the replica fan-out. We scrape every
# series of these and report per method/route, so we never depend on a guessed
# label value (e.g. exact gRPC method name).
#
# weaviate registers these with the metrics namespace ("weaviate"), so the names
# actually exposed at /metrics are "weaviate_<name>" — NOT the bare names the
# docs table lists. We match on suffix below so the scrape works regardless of
# the namespace prefix and never silently returns zero series.
REQUEST_HISTOGRAMS = [
    "grpc_server_request_duration_seconds",
    "http_request_duration_seconds",
]


def _canonical_metric(name: str) -> Optional[str]:
    """Map an exposed metric family name to its canonical (bare) form, honouring
    an optional namespace prefix such as "weaviate_". Returns None if it is not
    one of the request histograms we care about."""
    for m in REQUEST_HISTOGRAMS:
        if name == m or name.endswith("_" + m):
            return m
    return None


# ── Prometheus scraping ───────────────────────────────────────────────────────


class Hist:
    """One cumulative histogram series: le->count plus _sum/_count."""

    def __init__(self) -> None:
        self.buckets: Dict[float, float] = {}
        self.sum: float = 0.0
        self.count: float = 0.0

    def sub(self, older: "Hist") -> "Hist":
        d = Hist()
        les = set(self.buckets) | set(older.buckets)
        for le in les:
            d.buckets[le] = self.buckets.get(le, 0.0) - older.buckets.get(le, 0.0)
        d.sum = self.sum - older.sum
        d.count = self.count - older.count
        return d

    def quantile(self, q: float) -> Optional[float]:
        """Classic Prometheus histogram_quantile over cumulative buckets (seconds)."""
        if self.count <= 0 or not self.buckets:
            return None
        ordered = sorted(self.buckets.items(), key=lambda kv: kv[0])
        total = ordered[-1][1]  # +Inf bucket holds the full count
        if total <= 0:
            return None
        rank = q * total
        prev_le, prev_count = 0.0, 0.0
        for le, cum in ordered:
            if cum >= rank:
                if le == float("inf"):
                    # all mass in the overflow bucket; best estimate is the last
                    # finite boundary we saw
                    return prev_le if prev_le > 0 else None
                if cum == prev_count:
                    return le
                # linear interpolation within the bucket
                frac = (rank - prev_count) / (cum - prev_count)
                return prev_le + (le - prev_le) * frac
            prev_le, prev_count = le, cum
        return ordered[-1][0]

    def avg_ms(self) -> Optional[float]:
        if self.count <= 0:
            return None
        return (self.sum / self.count) * 1000.0


def _labels_key(labels: Dict[str, str]) -> Tuple[Tuple[str, str], ...]:
    # status/status_code varies per call and only fragments the series; drop it.
    drop = {"status", "status_code", "le"}
    return tuple(sorted((k, v) for k, v in labels.items() if k not in drop))


def scrape() -> Dict[str, Dict[Tuple, Hist]]:
    """Return {metric_name: {labelkey: Hist}} for the request histograms."""
    resp = requests.get(METRICS_URL, timeout=30)
    resp.raise_for_status()
    out: Dict[str, Dict[Tuple, Hist]] = {m: {} for m in REQUEST_HISTOGRAMS}
    for fam in text_string_to_metric_families(resp.text):
        canon = _canonical_metric(fam.name)
        if canon is None:
            continue
        series = out[canon]
        for s in fam.samples:
            labels = dict(s.labels)
            key = _labels_key(labels)
            h = series.setdefault(key, Hist())
            if s.name.endswith("_bucket"):
                le = labels.get("le", "+Inf")
                le_f = float("inf") if le in ("+Inf", "Inf") else float(le)
                # multiple raw series collapse into one key (status dropped) -> sum
                h.buckets[le_f] = h.buckets.get(le_f, 0.0) + s.value
            elif s.name.endswith("_sum"):
                h.sum += s.value
            elif s.name.endswith("_count"):
                h.count += s.value
    return out


def delta(
    after: Dict[str, Dict[Tuple, Hist]], before: Dict[str, Dict[Tuple, Hist]]
) -> Dict[str, Dict[Tuple, Hist]]:
    out: Dict[str, Dict[Tuple, Hist]] = {}
    for metric, series in after.items():
        out[metric] = {}
        for key, h in series.items():
            out[metric][key] = h.sub(before.get(metric, {}).get(key, Hist()))
    return out


def summarize(d: Dict[str, Dict[Tuple, Hist]]) -> Dict[str, List[dict]]:
    """Per metric, list the active series (count>0) with p50/p95/p99 in ms."""
    result: Dict[str, List[dict]] = {}
    for metric, series in d.items():
        rows = []
        for key, h in series.items():
            if h.count <= 0:
                continue
            labels = dict(key)
            rows.append(
                {
                    "labels": labels,
                    "count": int(h.count),
                    "avg_ms": _ms(h.avg_ms()),
                    "p50_ms": _ms(_sec(h.quantile(0.50))),
                    "p95_ms": _ms(_sec(h.quantile(0.95))),
                    "p99_ms": _ms(_sec(h.quantile(0.99))),
                }
            )
        rows.sort(key=lambda r: r["count"], reverse=True)
        result[metric] = rows
    return result


def _sec(v: Optional[float]) -> Optional[float]:
    return None if v is None else v * 1000.0  # seconds -> ms


def _ms(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 3)


# ── client-side latency helpers ───────────────────────────────────────────────


def pcts(samples_ms: List[float]) -> dict:
    if not samples_ms:
        return {"count": 0}
    s = sorted(samples_ms)

    def q(p: float) -> float:
        idx = min(len(s) - 1, int(round(p * (len(s) - 1))))
        return round(s[idx], 3)

    return {
        "count": len(s),
        "avg_ms": round(statistics.fmean(s), 3),
        "p50_ms": q(0.50),
        "p95_ms": q(0.95),
        "p99_ms": q(0.99),
        "max_ms": round(s[-1], 3),
    }


# ── workload ──────────────────────────────────────────────────────────────────


def rand_vec(rng: random.Random) -> List[float]:
    return [rng.random() for _ in range(DIM)]


def recreate_collection(client: weaviate.WeaviateClient) -> None:
    if client.collections.exists(COLLECTION):
        client.collections.delete(COLLECTION)
    client.collections.create(
        name=COLLECTION,
        replication_config=Configure.replication(factor=REPLICATION_FACTOR),
        vectorizer_config=Configure.Vectorizer.none(),
        properties=[
            Property(name="payload", data_type=DataType.TEXT),
            Property(name="seq", data_type=DataType.INT),
        ],
    )
    logger.info(f"created collection {COLLECTION} (rf={REPLICATION_FACTOR})")


def run_writes(coll, rng: random.Random) -> Tuple[List[str], List[float]]:
    """Batch-insert OBJECTS objects, returning their UUIDs and per-batch client latency (ms)."""
    uuids: List[str] = []
    latencies: List[float] = []
    pending: List[DataObject] = []

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        t0 = time.perf_counter()
        res = coll.data.insert_many(pending)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        if res.has_errors:
            # surface the first few errors loudly; a failing replica write must
            # never be silently treated as a latency sample
            first = list(res.errors.items())[:3]
            raise RuntimeError(f"insert_many had errors: {first}")
        pending = []

    for i in range(OBJECTS):
        u = str(uuidlib.UUID(int=rng.getrandbits(128)))
        uuids.append(u)
        pending.append(
            DataObject(
                uuid=u,
                properties={"payload": f"obj-{i}", "seq": i},
                vector=rand_vec(rng),
            )
        )
        if len(pending) >= BATCH_SIZE:
            flush()
    flush()
    return uuids, latencies


def run_reads(coll, uuids: List[str], rng: random.Random) -> List[float]:
    """fetch_object_by_id READS times (random existing UUIDs); returns client latency (ms)."""
    latencies: List[float] = []
    for _ in range(READS):
        u = uuids[rng.randrange(len(uuids))]
        t0 = time.perf_counter()
        obj = coll.query.fetch_object_by_id(u)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        if obj is None:
            raise RuntimeError(f"object {u} not found at consistency level read")
    return latencies


def bench_level(client: weaviate.WeaviateClient, level_name: str) -> dict:
    cl = _CL[level_name]
    logger.info(f"=== consistency level {level_name} ===")
    recreate_collection(client)
    coll = client.collections.get(COLLECTION).with_consistency_level(cl)
    rng = random.Random(SEED)

    # WRITE phase
    before = scrape()
    t0 = time.perf_counter()
    uuids, write_client_ms = run_writes(coll, rng)
    write_wall = time.perf_counter() - t0
    after = scrape()
    write_server = summarize(delta(after, before))

    # READ phase
    before = scrape()
    t0 = time.perf_counter()
    read_client_ms = run_reads(coll, uuids, rng)
    read_wall = time.perf_counter() - t0
    after = scrape()
    read_server = summarize(delta(after, before))

    return {
        "consistency_level": level_name,
        "write": {
            "objects": OBJECTS,
            "batch_size": BATCH_SIZE,
            "requests": len(write_client_ms),
            "wall_seconds": round(write_wall, 3),
            "objects_per_second": round(OBJECTS / write_wall, 1) if write_wall else None,
            "client_latency_ms": pcts(write_client_ms),
            "server_request_latency": write_server,
        },
        "read": {
            "reads": READS,
            "wall_seconds": round(read_wall, 3),
            "reads_per_second": round(READS / read_wall, 1) if read_wall else None,
            "client_latency_ms": pcts(read_client_ms),
            "server_request_latency": read_server,
        },
    }


# ── reporting ─────────────────────────────────────────────────────────────────


def _fmt(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:8.3f}"


def print_report(results: dict) -> None:
    print("\n" + "=" * 78)
    print(f" local-replica latency benchmark — weaviate {results['weaviate_version']}")
    print(
        f" cluster: {results['nodes']} nodes, rf={results['replication_factor']}, "
        f"coordinator=node-1 (always a local replica)"
    )
    print("=" * 78)
    for lvl in results["levels"]:
        name = lvl["consistency_level"]
        for phase in ("write", "read"):
            p = lvl[phase]
            cl_client = p["client_latency_ms"]
            print(
                f"\n[{phase.upper()} | CL={name}] client-side  "
                f"p50={_fmt(cl_client.get('p50_ms'))}  "
                f"p95={_fmt(cl_client.get('p95_ms'))}  "
                f"p99={_fmt(cl_client.get('p99_ms'))} ms"
                f"  ({cl_client.get('count', 0)} reqs)"
            )
            for metric, rows in p["server_request_latency"].items():
                for r in rows:
                    method = r["labels"].get("method") or r["labels"].get("route") or "?"
                    print(
                        f"    server {metric} method/route={method:<22} "
                        f"p50={_fmt(r['p50_ms'])} p95={_fmt(r['p95_ms'])} "
                        f"p99={_fmt(r['p99_ms'])} ms  (n={r['count']})"
                    )


def print_compare(current: dict, baseline_path: str) -> None:
    try:
        with open(baseline_path) as f:
            base = json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"COMPARE_TO={baseline_path!r} not usable: {e}")
        return

    def idx(res: dict) -> Dict[Tuple[str, str], dict]:
        m = {}
        for lvl in res["levels"]:
            for phase in ("write", "read"):
                m[(lvl["consistency_level"], phase)] = lvl[phase]["client_latency_ms"]
        return m

    cur, old = idx(current), idx(base)
    print("\n" + "=" * 78)
    print(f" DELTA vs baseline ({base.get('weaviate_version')}) — client-side p99 (ms)")
    print(" negative = faster on this build")
    print("=" * 78)
    for key in sorted(cur):
        c, b = cur[key], old.get(key, {})
        cp, bp = c.get("p99_ms"), b.get("p99_ms")
        if cp is None or bp is None:
            continue
        d = cp - bp
        pct = (d / bp * 100.0) if bp else 0.0
        print(
            f"  CL={key[0]:<6} {key[1]:<6} "
            f"baseline={bp:8.3f}  current={cp:8.3f}  delta={d:+8.3f} ({pct:+6.1f}%)"
        )


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    logger.info(
        f"connecting to {HTTP_HOST}:{HTTP_PORT} (grpc {GRPC_PORT}); " f"metrics at {METRICS_URL}"
    )
    # fail fast if monitoring is not actually exposed
    try:
        requests.get(METRICS_URL, timeout=10).raise_for_status()
    except Exception as e:  # noqa: BLE001
        logger.error(
            f"metrics endpoint {METRICS_URL} not reachable: {e}. "
            f"Is PROMETHEUS_MONITORING_ENABLED set on the cluster?"
        )
        return 2

    client = weaviate.connect_to_local(
        host=HTTP_HOST,
        port=HTTP_PORT,
        grpc_port=GRPC_PORT,
        additional_config=AdditionalConfig(timeout=Timeout(init=60, query=120, insert=300)),
    )
    try:
        levels = [bench_level(client, lvl) for lvl in CONSISTENCY_LEVELS]
    finally:
        client.close()

    results = {
        "weaviate_version": WEAVIATE_VERSION,
        "nodes": 3,
        "replication_factor": REPLICATION_FACTOR,
        "dim": DIM,
        "levels": levels,
    }
    with open(RESULTS_PATH, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"wrote {RESULTS_PATH}")

    print_report(results)
    if COMPARE_TO:
        print_compare(results, COMPARE_TO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
