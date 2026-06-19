"""
replication throughput bench
============================

Drive sustained concurrent load against a 3-node RF=3 cluster at each consistency
level and measure aggregate **throughput** (ops/sec) — the headline metric — plus
server-side replication latency as context.

Why throughput, why concurrent: per-op latency of single small ops on a shared CI
runner is dominated by scheduler/GC jitter, so it's unusable for an A/B. Throughput
sustained by many in-flight ops over a fixed window aggregates thousands of ops, so
a single window is already a stable number; a replication regression shows up as
lower sustained throughput.

One invocation runs ALL ROUNDS against an already-running cluster: it recreates +
preloads the collection once per consistency level, then runs ROUNDS timed windows
against the warm collection, and writes every round to that image's results file.
The orchestrator (replication_latency_bench.sh) brings each image up once and calls
this once per image. Aggregation/verdicts live in results_to_summary.py.
"""

import json
import os
import random
import statistics
import sys
import threading
import time
import uuid as uuidlib
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

import requests
from loguru import logger

import weaviate
from weaviate.classes.config import Configure, ConsistencyLevel, DataType, Property
from weaviate.classes.data import DataObject
from weaviate.classes.init import AdditionalConfig, Timeout
from weaviate.collections import Collection


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
# Coordinator node metrics endpoint (the node the client talks to).
METRICS_URL = os.getenv("METRICS_URL", f"http://{HTTP_HOST}:2112/metrics")
# All node metrics endpoints (node1 = the coordinator). Used to count the inbound
# replica HTTP calls each node receives — node1's own count is the self-calls the
# PR removes. Default matches docker-compose-replication.yml (2112/2113/2114).
METRICS_URLS = [
    u.strip()
    for u in (
        os.getenv("METRICS_URLS")
        or f"http://{HTTP_HOST}:2112/metrics,http://{HTTP_HOST}:2113/metrics,http://{HTTP_HOST}:2114/metrics"
    ).split(",")
    if u.strip()
]
NODE_LABELS = ["node1", "node2", "node3"]
REPLICA_HTTP_ROUTE = "/replicas/indices"  # inbound replica-write endpoint

COLLECTION = os.getenv("COLLECTION", "ReplThroughputBench")
REPLICATION_FACTOR = _int("REPLICATION_FACTOR", 3)
DIM = _int("DIM", 1536)

# Sustained-load knobs. CONCURRENCY in-flight workers hammer the cluster for
# WARMUP_SECONDS (discarded) then DURATION_SECONDS (timed). READ_POOL objects are
# pre-loaded so reads hit existing data.
CONCURRENCY = _int("CONCURRENCY", 16)
WARMUP_SECONDS = _int("WARMUP_SECONDS", 3)
DURATION_SECONDS = _int("DURATION_SECONDS", 12)
READ_POOL = _int("READ_POOL", 2000)
# Measurement windows per (CL); recreate + preload happens once per CL, then this
# many timed rounds run against the warm collection.
ROUNDS = _int("ROUNDS", 6)

# Latency probe: a few in-flight ops (no saturation/queueing) so each op's latency
# is its true critical path. This is where the local-replica short-circuit shows —
# at CL=ONE the write acks on the local leg, and skipping the loopback HTTP to self
# shortens that path. LAT_OPS timed ops per phase; p50/p95 reported.
LAT_CONCURRENCY = _int("LAT_CONCURRENCY", 2)
LAT_OPS = _int("LAT_OPS", 400)

CONSISTENCY_LEVELS = [
    c.strip().upper()
    for c in (os.getenv("CONSISTENCY") or "ONE,QUORUM,ALL").split(",")
    if c.strip()
]

RESULTS_PATH = os.getenv("RESULTS_PATH", "/workdir/results.json")
WEAVIATE_VERSION = os.getenv("WEAVIATE_VERSION", "unknown")
SEED = _int("SEED", 42)

_CL = {
    "ONE": ConsistencyLevel.ONE,
    "QUORUM": ConsistencyLevel.QUORUM,
    "ALL": ConsistencyLevel.ALL,
}


# ── Weaviate internal replication latency (coordinator duration histogram) ─────
#
# weaviate_replication_coordinator_{writes,reads}_duration_seconds is the latency
# Weaviate itself records for a coordinated replicated op, above the replica
# fan-out. We snapshot the full histogram (buckets + sum + count) before/after a
# window and compute p50/p95 from the bucket delta — a server-side, internal
# measure over the whole window's ops, far more stable than a client timing.


def scrape_repl_hist(url: str) -> Dict[str, dict]:
    """{'write'|'read': {'buckets': {le: cum_count}, 'sum': s, 'count': c}}."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    h = {k: {"buckets": {}, "sum": 0.0, "count": 0.0} for k in ("write", "read")}
    for line in resp.text.splitlines():
        if line.startswith("#") or not line:
            continue
        for key, stem in (("write", "writes"), ("read", "reads")):
            base = f"replication_coordinator_{stem}_duration_seconds"
            val = float(line.rsplit(" ", 1)[1])
            if f"{base}_bucket{{" in line:
                le = line.split('le="', 1)[1].split('"', 1)[0]
                lef = float("inf") if le in ("+Inf", "Inf") else float(le)
                h[key]["buckets"][lef] = h[key]["buckets"].get(lef, 0.0) + val
            elif f"{base}_sum" in line:
                h[key]["sum"] += val
            elif f"{base}_count" in line:
                h[key]["count"] += val
    return h


def _hist_delta(after: Dict, before: Dict, key: str) -> dict:
    a, b = after[key], before[key]
    out = {"buckets": {}, "sum": a["sum"] - b["sum"], "count": a["count"] - b["count"]}
    for le in set(a["buckets"]) | set(b["buckets"]):
        out["buckets"][le] = a["buckets"].get(le, 0.0) - b["buckets"].get(le, 0.0)
    return out


def scrape_replica_calls(url: str) -> float:
    """Cumulative inbound replica-write HTTP requests served by one node (count over
    the /replicas/indices route). The coordinator's own count is its self-calls —
    the thing the PR removes."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    total = 0.0
    for line in resp.text.splitlines():
        if line.startswith("#") or not line:
            continue
        if "http_request_duration_seconds_count" in line and REPLICA_HTTP_ROUTE in line:
            total += float(line.rsplit(" ", 1)[1])
    return total


def _replica_snapshot() -> List[float]:
    return [scrape_replica_calls(u) for u in METRICS_URLS]


def scrape_proc(url: str) -> Tuple[float, float]:
    """(process_cpu_seconds_total, go_memstats_alloc_bytes_total) for one node —
    the actual CPU + memory work it does, used to measure what the self-call costs."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    cpu = alloc = 0.0
    for line in resp.text.splitlines():
        if line.startswith("process_cpu_seconds_total "):
            cpu = float(line.split()[1])
        elif line.startswith("go_memstats_alloc_bytes_total "):
            alloc = float(line.split()[1])
    return cpu, alloc


def _proc_snapshot() -> List[Tuple[float, float]]:
    return [scrape_proc(u) for u in METRICS_URLS]


def _hist_quantile(h: dict, q: float) -> Optional[float]:
    """Standard Prometheus histogram_quantile over cumulative buckets → ms."""
    buckets = h.get("buckets", {})
    if h.get("count", 0) <= 0 or not buckets:
        return None
    ordered = sorted(buckets.items())
    total = ordered[-1][1]
    if total <= 0:
        return None
    rank = q * total
    prev_le, prev_c = 0.0, 0.0
    for le, cum in ordered:
        if cum >= rank:
            if le == float("inf"):
                return round(prev_le * 1000.0, 3) if prev_le > 0 else None
            if cum == prev_c:
                return round(le * 1000.0, 3)
            frac = (rank - prev_c) / (cum - prev_c)
            return round((prev_le + (le - prev_le) * frac) * 1000.0, 3)
        prev_le, prev_c = le, cum
    return round(ordered[-1][0] * 1000.0, 3)


# ── workload ──────────────────────────────────────────────────────────────────


def _rand_vec(rng: random.Random) -> List[float]:
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


def _pool_uuid(i: int) -> str:
    return str(uuidlib.uuid5(uuidlib.NAMESPACE_DNS, f"{COLLECTION}-pool-{i}"))


def preload_read_pool(coll: Collection) -> None:
    """Insert READ_POOL deterministic objects so the read phase hits existing data."""
    rng = random.Random(SEED)
    with coll.batch.fixed_size(batch_size=200) as batch:
        for i in range(READ_POOL):
            batch.add_object(
                uuid=_pool_uuid(i),
                properties={"payload": f"pool-{i}", "seq": i},
                vector=_rand_vec(rng),
            )
    if coll.batch.failed_objects:
        raise RuntimeError(f"read-pool preload failed: {coll.batch.failed_objects[:3]}")


def _run_load(do_op: Callable[[random.Random], None], duration_s: float) -> Tuple[int, float]:
    """Run CONCURRENCY workers calling do_op() in a loop until the deadline.
    Returns (total ops completed, wall seconds elapsed)."""
    deadline = time.perf_counter() + duration_s

    def worker() -> int:
        rng = random.Random(SEED + threading.get_ident())
        n = 0
        while time.perf_counter() < deadline:
            do_op(rng)
            n += 1
        return n

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        counts = [f.result() for f in [ex.submit(worker) for _ in range(CONCURRENCY)]]
    elapsed = time.perf_counter() - t0
    return sum(counts), elapsed


def _pctile(sorted_ms: List[float], q: float) -> Optional[float]:
    if not sorted_ms:
        return None
    return round(sorted_ms[min(len(sorted_ms) - 1, int(q * len(sorted_ms)))], 3)


def _latency_probe(do_op: Callable[[random.Random], None]) -> List[float]:
    """Run LAT_OPS ops across LAT_CONCURRENCY workers (low, no saturation) and
    return the sorted per-op latencies (ms)."""
    per = max(1, LAT_OPS // LAT_CONCURRENCY)

    def worker() -> List[float]:
        rng = random.Random(SEED + threading.get_ident())
        out = []
        for _ in range(per):
            t0 = time.perf_counter()
            do_op(rng)
            out.append((time.perf_counter() - t0) * 1000.0)
        return out

    with ThreadPoolExecutor(max_workers=LAT_CONCURRENCY) as ex:
        results = [f.result() for f in [ex.submit(worker) for _ in range(LAT_CONCURRENCY)]]
    return sorted(x for r in results for x in r)


def bench_level(coll_writer: Collection, coll_reader: Collection) -> dict:
    def write_op(rng: random.Random) -> None:
        u = str(uuidlib.UUID(int=rng.getrandbits(128)))
        res = coll_writer.data.insert_many(
            [DataObject(uuid=u, properties={"payload": "x", "seq": 0}, vector=_rand_vec(rng))]
        )
        if res.has_errors:
            raise RuntimeError(f"insert error: {list(res.errors.items())[:1]}")

    def read_op(rng: random.Random) -> None:
        obj = coll_reader.query.fetch_object_by_id(_pool_uuid(rng.randrange(READ_POOL)))
        if obj is None:
            raise RuntimeError("read miss on a pooled object")

    # WRITE — latency probe at low concurrency → true per-op critical path, where
    # the short-circuit shows. Snapshot Weaviate's internal replication-duration
    # histogram around the probe (unloaded, so it isn't masked by queueing) and
    # take p50/p95 from the bucket delta. Then saturating throughput.
    _run_load(write_op, WARMUP_SECONDS)
    rh_before = scrape_repl_hist(METRICS_URL)
    w_lat = _latency_probe(write_op)
    w_int = _hist_delta(scrape_repl_hist(METRICS_URL), rh_before, "write")
    # count inbound replica HTTP calls per node across the throughput window;
    # node1's (coordinator) self-calls are what the PR eliminates.
    rep_before = _replica_snapshot()
    proc_before = _proc_snapshot()
    w_ops, w_elapsed = _run_load(write_op, DURATION_SECONDS)
    rep_after = _replica_snapshot()
    proc_after = _proc_snapshot()
    n = len(METRICS_URLS)
    replica_http = (
        {NODE_LABELS[i]: round((rep_after[i] - rep_before[i]) / w_ops, 3) for i in range(n)}
        if w_ops
        else {}
    )
    # downstream impact of removing the self-call: CPU + bytes allocated per write,
    # per node. The coordinator (node1) skips serializing the object for its self
    # leg, so its CPU/alloc per write should drop if that cost is material.
    cpu_per_write = (
        {
            NODE_LABELS[i]: round((proc_after[i][0] - proc_before[i][0]) / w_ops * 1000.0, 4)
            for i in range(n)
        }
        if w_ops
        else {}
    )
    alloc_per_write = (
        {
            NODE_LABELS[i]: round((proc_after[i][1] - proc_before[i][1]) / w_ops / 1024.0, 2)
            for i in range(n)
        }
        if w_ops
        else {}
    )

    # READ — same: internal histogram around the probe, then throughput.
    _run_load(read_op, WARMUP_SECONDS)
    rhr_before = scrape_repl_hist(METRICS_URL)
    r_lat = _latency_probe(read_op)
    r_int = _hist_delta(scrape_repl_hist(METRICS_URL), rhr_before, "read")
    r_ops, r_elapsed = _run_load(read_op, DURATION_SECONDS)

    return {
        "write": {
            "throughput_ops": round(w_ops / w_elapsed, 3),
            "ops": w_ops,
            "internal_p50_ms": _hist_quantile(w_int, 0.50),
            "internal_p95_ms": _hist_quantile(w_int, 0.95),
            "latency_p50_ms": _pctile(w_lat, 0.50),
            "latency_p95_ms": _pctile(w_lat, 0.95),
            "replica_http_per_op": replica_http,
            "cpu_ms_per_write": cpu_per_write,
            "alloc_kb_per_write": alloc_per_write,
        },
        "read": {
            "throughput_ops": round(r_ops / r_elapsed, 3),
            "ops": r_ops,
            "internal_p50_ms": _hist_quantile(r_int, 0.50),
            "internal_p95_ms": _hist_quantile(r_int, 0.95),
            "latency_p50_ms": _pctile(r_lat, 0.50),
            "latency_p95_ms": _pctile(r_lat, 0.95),
        },
    }


def main() -> int:
    if not CONSISTENCY_LEVELS:
        logger.error("no valid CONSISTENCY levels parsed")
        return 2
    logger.info(
        f"{WEAVIATE_VERSION} @ {HTTP_HOST}:{HTTP_PORT} — {ROUNDS} rounds "
        f"(concurrency={CONCURRENCY}, warmup={WARMUP_SECONDS}s, window={DURATION_SECONDS}s)"
    )
    try:
        requests.get(METRICS_URL, timeout=10).raise_for_status()
    except Exception as e:  # noqa: BLE001
        logger.error(f"metrics endpoint {METRICS_URL} not reachable: {e}")
        return 2

    client = weaviate.connect_to_local(
        host=HTTP_HOST,
        port=HTTP_PORT,
        grpc_port=GRPC_PORT,
        additional_config=AdditionalConfig(timeout=Timeout(init=60, query=120, insert=300)),
    )
    # Recreate + preload the read pool ONCE per CL (expensive), then run all ROUNDS
    # measurement windows against the warm collection. per_cl[cl][r] is round r's
    # result for that CL; we transpose to rounds[r].levels afterwards.
    per_cl: Dict[str, list] = {}
    try:
        for lvl in CONSISTENCY_LEVELS:
            logger.info(f"=== CL={lvl}: recreate + preload, then {ROUNDS} rounds ===")
            recreate_collection(client)
            base = client.collections.get(COLLECTION)
            preload_read_pool(base)
            coll = base.with_consistency_level(_CL[lvl])
            per_cl[lvl] = []
            for r in range(ROUNDS):
                result = bench_level(coll, coll)
                result["consistency_level"] = lvl
                per_cl[lvl].append(result)
                logger.info(
                    f"CL={lvl} round {r + 1}/{ROUNDS}: write {result['write']['throughput_ops']} "
                    f"ops/s, read {result['read']['throughput_ops']} ops/s"
                )
    finally:
        client.close()

    doc = {
        "weaviate_version": WEAVIATE_VERSION,
        "nodes": 3,
        "replication_factor": REPLICATION_FACTOR,
        "dim": DIM,
        "concurrency": CONCURRENCY,
        "duration_seconds": DURATION_SECONDS,
        "rounds": [
            {"round": r + 1, "levels": [per_cl[lvl][r] for lvl in CONSISTENCY_LEVELS]}
            for r in range(ROUNDS)
        ],
    }
    with open(RESULTS_PATH, "w") as f:
        json.dump(doc, f, indent=2)
    logger.info(f"wrote {ROUNDS} rounds to {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
