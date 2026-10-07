import argparse
import csv as csv_module
import glob
import os
import re
import sys
import io
import time
import threading
import asyncio
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

from loguru import logger

try:
    # Prefer explicit import alias to satisfy request wording
    from weaviate import (
        connect_to_local as weaviate_connect_to_local,
        use_async_with_local as weaviate_use_async_with_local,
    )
    from weaviate.collections.classes.config import ConsistencyLevel
except Exception:  # pragma: no cover
    # Fallback to module attr if alias import style is not available
    import weaviate  # type: ignore
    from weaviate.collections.classes.config import ConsistencyLevel

    def weaviate_connect_to_local():  # type: ignore
        return weaviate.connect_to_local()  # type: ignore

    def weaviate_use_async_with_local():  # type: ignore
        return weaviate.use_async_with_local()  # type: ignore


from weaviate_cli.managers.collection_manager import CollectionManager
from weaviate_cli.managers.data_manager import DataManager
from weaviate_cli.managers.benchmark_manager import PER_REQUEST_TIMEOUT_S, BenchmarkQPSManager

from server_metrics import PodSnapshot, snapshot_pods, summarise_window
from validation import (
    annotate_csvs_with_restart,
    get_env_float,
    get_env_int,
    validate_benchmark_csv,
)


# weaviate-cli reports ingestion and benchmark errors by printing them, and the
# counts below are recovered from that text. contextlib.redirect_stdout swaps the
# process-global sys.stdout, so with four ingestion threads and four gathered
# benchmark coroutines the printers interleave and one collection's errors land in
# another's buffer. A ContextVar is per thread and is copied into each asyncio
# Task, so it attributes output correctly in both cases. Output still reaches the
# real stdout, which the redirect used to swallow for the whole run.
_capture: ContextVar[Optional[io.StringIO]] = ContextVar("capture", default=None)


class _RoutedStdout:
    """Tees writes to the calling context's buffer, when it registered one."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.unattributed = io.StringIO()

    def write(self, s: str) -> int:
        buf = _capture.get()
        (buf if buf is not None else self.unattributed).write(s)
        return self._real.write(s)

    def flush(self) -> None:
        self._real.flush()

    def isatty(self) -> bool:
        return False


_routed_stdout = _RoutedStdout(sys.stdout)
sys.stdout = _routed_stdout  # type: ignore[assignment]


@contextmanager
def capture_stdout() -> Iterator[io.StringIO]:
    """Collect what this thread or task prints, without hiding it from the log."""
    buf = io.StringIO()
    token = _capture.set(buf)
    try:
        yield buf
    finally:
        _capture.reset(token)


def scrape_pod_metrics(ns: str) -> Dict[str, str]:
    """Each pod's metrics, keyed by pod. Served through the API proxy so no
    port-forward is needed and the pinned context still applies.

    Kept separate rather than concatenated: these counters reset when a pod
    restarts, so the delta has to be taken per pod before anything is summed.
    See server_metrics.summarise_window.
    """
    texts: Dict[str, str] = {}
    for pod in get_pod_names(ns):
        if not pod.startswith("weaviate-"):
            continue
        out = subprocess.run(
            kubectl("get", "--raw", f"/api/v1/namespaces/{ns}/pods/{pod}:2112/proxy/metrics"),
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0:
            texts[pod] = out.stdout
        else:
            logger.warning("Could not scrape {pod} metrics: {e}", pod=pod, e=out.stderr[:120])
    return texts


def snapshot_server_metrics(
    ns: str, attempts: int = 5, pause_s: float = 3.0
) -> Optional[Dict[str, PodSnapshot]]:
    """A snapshot covering every weaviate pod, or None if one never answered.

    A partial snapshot cannot be used. A pod missing from the baseline looks
    newly started to summarise_window, which then counts its lifetime history as
    window traffic; a pod missing afterwards drops its share of the window. So
    retry until the scrape is complete rather than judge either.
    """
    texts: Dict[str, str] = {}
    expected: List[str] = []
    for attempt in range(attempts):
        expected = get_pod_names(ns)
        texts = scrape_pod_metrics(ns)
        if expected and set(expected) <= set(texts):
            return snapshot_pods(texts)
        if attempt < attempts - 1:
            time.sleep(pause_s)
    logger.error(
        "Could not scrape metrics from every pod after {n} attempts: missing {m}",
        n=attempts,
        m=sorted(set(expected) - set(texts)) or "all (no pods found)",
    )
    return None


# Production's worst signal was p999 46s, and those were aggregates: CountObjects
# pulls with a one-minute budget, so a replica that cannot serve is waited on
# rather than skipped. Hybrid search takes the ordinary path, where one restarting
# node of three still leaves a quorum and a spare, which is why searches alone
# never showed this. Aggregates are not in queries_durations_ms either -- only
# get_graphql is instrumented -- so they have to be timed from here.
def aggregate_once(class_name: str, timeout_s: float, port: int = 8080) -> Tuple[float, bool]:
    """Run one count aggregate; returns (elapsed_ms, ok)."""
    graphql_class = class_name[:1].upper() + class_name[1:]
    started = time.time()
    try:
        resp = requests.post(
            f"http://localhost:{port}/v1/graphql",
            json={"query": "{ Aggregate { %s { meta { count } } } }" % graphql_class},
            timeout=timeout_s,
        )
        elapsed = (time.time() - started) * 1000.0
        ok = resp.status_code == 200 and "errors" not in (resp.json() or {})
        return elapsed, ok
    except Exception:
        return (time.time() - started) * 1000.0, False


LOCAL_CONTEXT_PREFIXES = ("kind-", "minikube", "docker-desktop", "k3d-")


def resolve_kube_context() -> str:
    ctx = os.getenv("K8S_CONTEXT", "").strip()
    if not ctx:
        ctx = subprocess.run(
            ["kubectl", "config", "current-context"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    if not ctx:
        raise SystemExit("No kubectl context: set K8S_CONTEXT to the local test cluster")
    if os.getenv("ALLOW_NON_LOCAL_CLUSTER", "").lower() == "true":
        logger.warning("Running against non-local context {ctx} by opt-in", ctx=ctx)
        return ctx
    if not ctx.startswith(LOCAL_CONTEXT_PREFIXES):
        raise SystemExit(
            f"Refusing to run against kubectl context {ctx!r}: this test deletes pods "
            f"and restarts the weaviate StatefulSet. Expected a local cluster "
            f"({', '.join(LOCAL_CONTEXT_PREFIXES)}...), or set "
            f"ALLOW_NON_LOCAL_CLUSTER=true."
        )
    return ctx


KUBE_CONTEXT = resolve_kube_context()


def kubectl(*args: str) -> List[str]:
    """kubectl argv with the context pinned, so it cannot drift mid-run."""
    return ["kubectl", "--context", KUBE_CONTEXT, *args]


def get_raft_leader(host: str = "localhost", port: int = 8080) -> Optional[str]:
    """Return the RAFT leaderId from /v1/cluster/statistics, or None on failure."""
    url = f"http://{host}:{port}/v1/cluster/statistics"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        for entry in resp.json().get("statistics", []):
            leader = entry.get("leaderId")
            if leader:
                return leader
    except Exception as e:
        logger.warning("Could not determine RAFT leader from {url}: {e}", url=url, e=e)
    return None


def get_pod_names(ns: str) -> List[str]:
    """Return the names of all weaviate StatefulSet pods currently known to kubectl."""
    result = subprocess.run(
        kubectl(
            "get",
            "pods",
            "-n",
            ns,
            "-o",
            "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    return [p for p in result.stdout.strip().splitlines() if p.startswith("weaviate-")]


def restart_pod_and_wait(pod_name: str, ns: str, timeout_sec: int = 120) -> bool:
    """
    Delete *pod_name* (the StatefulSet controller will recreate it) and block
    until the pod is Running and Ready again.  Returns True on success.
    """
    logger.info("Restarting pod {pod}...", pod=pod_name)

    del_result = subprocess.run(
        kubectl("delete", "pod", pod_name, "-n", ns),
        capture_output=True,
        text=True,
        check=False,
    )
    if del_result.returncode != 0:
        logger.error(
            "Failed to delete pod {pod}: {err}", pod=pod_name, err=del_result.stderr.strip()
        )
        return False

    # Wait for the old pod instance to disappear before watching for Ready
    subprocess.run(
        kubectl("wait", f"pod/{pod_name}", "--for=delete", "--timeout=60s", "-n", ns),
        capture_output=True,
        text=True,
        check=False,
    )

    wait_result = subprocess.run(
        kubectl(
            "wait",
            f"pod/{pod_name}",
            "--for=condition=Ready",
            f"--timeout={timeout_sec}s",
            "-n",
            ns,
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if wait_result.returncode != 0:
        logger.error("Pod {pod} did not become Ready within {t}s", pod=pod_name, t=timeout_sec)
        return False

    logger.info("Pod {pod} is Ready.", pod=pod_name)
    return True


def wait_for_statefulset_ready(ns: str) -> bool:
    timeout_sec = 300  # 5 minutes
    logger.info(f"Waiting for statefulset to be ready (timeout: {timeout_sec} seconds)...")
    cmd = kubectl(
        "rollout",
        "status",
        "sts/weaviate",
        f"--timeout={timeout_sec}s",
    )
    if ns:
        cmd.extend(["-n", ns])
    logger.info("Executing: {cmd}", cmd=" ".join(cmd))
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        logger.info("kubectl stdout: {out}", out=result.stdout.strip())
    if result.stderr:
        logger.warning("kubectl stderr: {err}", err=result.stderr.strip())
    if result.returncode != 0:
        logger.error("kubectl exited with code {code}", code=result.returncode)

    logger.info("Statefulset is ready (or rollout command completed).")
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rolling-restart / single-pod-restart QPS chaos test",
    )
    parser.add_argument(
        "variant",
        choices=["rolling-restart", "single-pod-restart"],
        help=(
            "rolling-restart: rolling update of all pods while benchmarks run. "
            "single-pod-restart: restart one non-leader pod then the leader pod, "
            "waiting for each to be Ready before proceeding."
        ),
    )
    args = parser.parse_args()
    variant = args.variant

    test_start_time = time.time()

    # Read env-driven knobs
    objects_per_class = get_env_int("OBJECTS_PER_CLASS", 1000)
    batch_size = get_env_int("BATCH_SIZE", 100)
    collection_prefix = os.getenv("COLLECTION_PREFIX", "rrha_")
    ns = os.getenv("K8S_NAMESPACE", "weaviate")
    benchmark_qps = get_env_int("BENCHMARK_QPS", 20)
    sustained_writes = os.getenv("SUSTAINED_WRITES", "true").strip().lower() == "true"
    # ~10 writes/s per collection, so ~40/s across the four. 500 objects every 5s
    # was 400/s -- 13x what production was taking while it was rolled -- and it
    # held queries to 14 of a 20 QPS target, which invalidates the run.
    sustained_write_objects = get_env_int("SUSTAINED_WRITE_OBJECTS", 100)
    sustained_write_pause_s = get_env_int("SUSTAINED_WRITE_PAUSE_S", 10)

    # Remove any leftover benchmark_results_* files from previous local runs so
    # the validation step never accidentally picks up stale CSVs.
    stale = glob.glob("benchmark_results_*")
    if stale:
        logger.info(
            "Removing {n} stale benchmark_results_* file(s) from previous runs", n=len(stale)
        )
        for p in stale:
            os.remove(p)

    # Connect to local Weaviate
    logger.info("Connecting to local Weaviate...")
    client = weaviate_connect_to_local()

    collection_manager = CollectionManager(client)
    data_manager = DataManager(client)

    failure_state = {"failed": False}

    # Define collections
    # All use vectorizer="transformers" as requested
    collections: List[Dict[str, Any]] = [
        {
            "name": f"{collection_prefix}no_mt_sync_hnsw_pq",
            "vector_index": "hnsw_pq",
            "multitenant": False,
            "async_enabled": False,
        },
        {
            "name": f"{collection_prefix}no_mt_async_hnsw_rq",
            "vector_index": "hnsw_rq",
            "multitenant": False,
            "async_enabled": True,
        },
        {
            "name": f"{collection_prefix}mt_sync_hnsw_sq",
            "vector_index": "hnsw_sq",
            "multitenant": True,
            "async_enabled": False,
        },
        {
            "name": f"{collection_prefix}mt_async_hnsw_bq",
            "vector_index": "hnsw_bq",
            "multitenant": True,
            "async_enabled": True,
        },
    ]

    # Create collections
    for cfg in collections:
        try:
            if client.collections.exists(cfg["name"]):
                logger.info(f"Collection {cfg['name']} already exists, cleaning it up...")
                client.collections.delete(cfg["name"])
            logger.info(
                "Creating collection {name} (index={index}, MT={mt}, async={async_enabled})",
                name=cfg["name"],
                index=cfg["vector_index"],
                mt=cfg["multitenant"],
                async_enabled=cfg["async_enabled"],
            )
            collection_manager.create_collection(
                collection=cfg["name"],
                vector_index=cfg["vector_index"],
                replication_factor=3,
                multitenant=cfg["multitenant"],
                auto_tenant_creation=cfg["multitenant"],
                async_enabled=cfg["async_enabled"],
                replication_deletion_strategy="no_automated_resolution",
                vectorizer="transformers",
                # Shards are loaded lazily, so shard size decides how long a pod
                # that already reports ready still cannot answer -- the window this
                # test depends on. Spreading the same objects over more shards
                # shrinks it: 15k over 4 shards loads in well under a second.
                shards=(0 if cfg["multitenant"] else get_env_int("NO_MT_SHARDS", 4)),
            )
        except Exception as e:  # pragma: no cover
            logger.exception(f"Failed to create collection {cfg['name']}: {e}")

    # Start background ingestion for each collection
    ingestion_threads: List[threading.Thread] = []

    # Production was under continuous writes while it was rolled, which is what
    # keeps schema versions advancing and so what makes a restarting node
    # measurably behind. One bounded pass finishes long before the window ends
    # and leaves the rest of the run read-only, so keep writing in rounds until
    # the benchmark is done.
    # Only sustained rounds that overlap the restart window feed the write gate.
    # The seeding round writes objects_per_class (15k x 4 collections) against
    # sustained_write_objects for later rounds, so counting it would leave 60k
    # pre-restart attempts in the denominator and dilute a real refusal rate into
    # a pass. Seeding is waited on before the benchmark starts, so it never
    # overlaps the window.
    write_stats: Dict[str, Dict[str, int]] = {}
    writes_done = threading.Event()
    measure_writes = threading.Event()
    seeded = {cfg["name"]: threading.Event() for cfg in collections}

    def ingest_round(name: str, auto_tenants: int, limit: int) -> int:
        """One pass of create_data; returns the error count it reported."""
        with capture_stdout() as buf:
            data_manager.create_data(
                collection=name,
                limit=limit,
                consistency_level=os.getenv("INGESTION_CONSISTENCY_LEVEL", "quorum"),
                randomize=True,
                auto_tenants=auto_tenants,
                batch_size=min(batch_size, limit),
                wait_for_indexing=True,
            )
        m = re.search(r"Encountered\s+(\d+)\s+total errors", buf.getvalue())
        return int(m.group(1)) if m else 0

    def ingest_target(name: str, is_mt: bool) -> None:
        auto_tenants = 1 if is_mt else 0
        rounds = 0
        stats = {"errors": 0, "attempted": 0, "window_errors": 0, "window_attempted": 0}
        logger.info(
            "Starting ingestion for {name} (limit={limit}, tenants={auto_tenants}, "
            "sustained={sustained})",
            name=name,
            limit=objects_per_class,
            auto_tenants=auto_tenants,
            sustained=sustained_writes,
        )
        while True:
            # The first round seeds the collection; later rounds only need to keep
            # writes in flight, so they are small and paced. Flat-out rounds
            # starve the query path and invalidate the run.
            limit = objects_per_class if rounds == 0 else sustained_write_objects
            seeding = rounds == 0
            errors = 0
            try:
                errors = ingest_round(name, auto_tenants, limit)
                rounds += 1
            except Exception as e:  # pragma: no cover
                # A write refused mid-restart is the thing being measured, not a
                # reason to stop writing.
                logger.warning("Ingestion round for {name} failed: {e}", name=name, e=e)
                errors = 1
                rounds += 1
            finally:
                if seeding:
                    seeded[name].set()
            stats["errors"] += errors
            stats["attempted"] += limit
            # measure_writes is only ever set, so checking it after the round
            # catches every round that overlapped the window, including one that
            # was in flight when it opened. Sustained rounds are small, so the
            # pre-window part of that one is negligible.
            if not seeding and measure_writes.is_set():
                stats["window_errors"] += errors
                stats["window_attempted"] += limit
            if not sustained_writes or writes_done.is_set():
                break
            if writes_done.wait(timeout=sustained_write_pause_s):
                break

        write_stats[name] = stats
        logger.info(
            "Ingestion for {name} finished: {r} round(s), {e} error(s) of {a} attempted "
            "({we} of {wa} inside the restart window)",
            name=name,
            r=rounds,
            e=stats["errors"],
            a=stats["attempted"],
            we=stats["window_errors"],
            wa=stats["window_attempted"],
        )

    for cfg in collections:
        is_mt = cfg["multitenant"] != False
        t = threading.Thread(
            target=ingest_target,
            args=(cfg["name"], is_mt),
            daemon=True,  # allow process to exit after timeout
            name=f"ingest-{cfg['name']}",
        )
        t.start()
        ingestion_threads.append(t)

    # Let seeding finish before the benchmark and the window start, so the
    # baseline is not measured under the seed load and no seed write is
    # attributed to the restart.
    seed_timeout_s = get_env_int("SEED_TIMEOUT_S", 900)
    logger.info(
        "Ingestion started for all collections. Waiting up to {t}s for seeding...",
        t=seed_timeout_s,
    )
    seed_deadline = time.time() + seed_timeout_s
    unseeded = [n for n, ev in seeded.items() if not ev.wait(max(0.0, seed_deadline - time.time()))]
    if unseeded:
        logger.error(
            "Seeding did not finish within {t}s for {c}; seed writes overlap the run",
            t=seed_timeout_s,
            c=unseeded,
        )
        failure_state["failed"] = True

    # Start QPS benchmarks for all collections in parallel (daemon thread, non-blocking)
    logger.info("Starting QPS benchmarks for all collections...")

    timeout_counts: Dict[str, int] = {}

    # Aggregates run alongside the search load, at a low rate -- the point is to
    # keep the aggregate path in use across the restart, not to add pressure.
    agg_stop = threading.Event()
    agg_slow_ms = get_env_float("AGGREGATE_SLOW_MS", 5000.0)
    agg_interval_s = get_env_float("AGGREGATE_INTERVAL_S", 1.0)
    agg_timeout_s = get_env_float("AGGREGATE_TIMEOUT_S", 90.0)
    agg_stats = {"count": 0, "slow": 0, "failed": 0, "max_ms": 0.0}
    agg_classes = [cfg["name"] for cfg in collections if not cfg["multitenant"]]

    def aggregate_driver() -> None:
        i = 0
        while not agg_stop.is_set():
            cls = agg_classes[i % len(agg_classes)]
            elapsed, ok = aggregate_once(cls, agg_timeout_s)
            agg_stats["count"] += 1
            agg_stats["max_ms"] = max(agg_stats["max_ms"], elapsed)
            if not ok:
                agg_stats["failed"] += 1
            if elapsed > agg_slow_ms:
                agg_stats["slow"] += 1
                logger.warning(
                    "Aggregate on {cls} took {ms:.0f}ms (ok={ok})", cls=cls, ms=elapsed, ok=ok
                )
            i += 1
            agg_stop.wait(timeout=agg_interval_s)

    agg_thread = threading.Thread(target=aggregate_driver, name="aggregates", daemon=True)

    async def run_all_benchmarks() -> None:
        async def benchmark_one(collection_name: str) -> None:
            async_client = weaviate_use_async_with_local()
            manager = BenchmarkQPSManager(async_client)
            with capture_stdout() as buf:
                await manager.run_benchmark(
                    collection=collection_name,
                    max_duration=get_env_int("BENCHMARK_DURATION", 120),
                    query_type="hybrid",
                    query_terms=["test"],
                    file_alias=collection_name,
                    fail_on_timeout=False,
                    warmup_duration=0,
                    qps=benchmark_qps,
                    output="csv",
                    generate_graph=True,
                )
            out = buf.getvalue()
            n_timeouts = out.count("timed out after")
            timeout_counts[collection_name] = n_timeouts
            if n_timeouts:
                logger.error(
                    "{n} query(s) exceeded the {t}s per-request timeout for {name}",
                    n=n_timeouts,
                    t=PER_REQUEST_TIMEOUT_S,
                    name=collection_name,
                )
            if "No successful queries were completed" in out or "generated an exception" in out:
                logger.error(
                    "Benchmark detected failures for collection {name}.",
                    name=collection_name,
                )
                failure_state["failed"] = True

        tasks = [benchmark_one(cfg["name"]) for cfg in collections]
        await asyncio.gather(*tasks)

    bench_thread = threading.Thread(
        target=lambda: asyncio.run(run_all_benchmarks()),
        name="benchmarks-qps",
        daemon=False,
    )
    bench_thread.start()

    restart_delay = get_env_int("RESTART_DELAY_SECONDS", 15)
    logger.info("Waiting {d}s before triggering disruption...", d=restart_delay)
    time.sleep(restart_delay)

    # Baseline the server's own counters. Everything accrued after this point is
    # the restart window.
    pre_snapshot = snapshot_server_metrics(ns)

    # The window opens here, so the aggregate driver and the write accounting
    # start here too. Starting the driver with the benchmarks put the warm-up's
    # first aggregates -- cold caches over freshly seeded data -- into a gate
    # that tolerates none, and agg_stats has no baseline to subtract.
    measure_writes.set()
    agg_thread.start()

    # --- Disruption phase (variant-specific) ---
    # restart_events accumulates (timestamp, event_name) pairs; each entry will
    # become one sentinel row in the benchmark CSVs so the pre/post split is
    # unambiguous when validating or inspecting artifacts manually.
    leader_before: Optional[str] = None
    leader_after: Optional[str] = None
    restart_events: List[Tuple[float, str]] = []

    if variant == "rolling-restart":
        leader_before = get_raft_leader()
        logger.info("=== RAFT leader before rolling restart: {l} ===", l=leader_before)

        rolling_restart_time = time.time()
        restart_events.append((rolling_restart_time, "rolling_restart_event"))

        logger.info("Triggering rolling restart for sts/weaviate...")
        cmd = kubectl("rollout", "restart", "sts/weaviate")
        if ns:
            cmd.extend(["-n", ns])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.stdout:
            logger.info("kubectl stdout: {out}", out=result.stdout.strip())
        if result.stderr:
            logger.warning("kubectl stderr: {err}", err=result.stderr.strip())
        if result.returncode != 0:
            logger.error("kubectl rollout restart exited with code {code}", code=result.returncode)
            failure_state["failed"] = True

        # Check leader after all pods are back (statefulset ready is called later,
        # but the cluster is functional once the rollout completes)
        leader_after = get_raft_leader()

    else:  # single-pod-restart
        pods = get_pod_names(ns)
        if not pods:
            logger.error("Could not retrieve pod list — aborting disruption")
            failure_state["failed"] = True
            bench_thread.join()
        else:
            leader_before = get_raft_leader()
            logger.info("=== RAFT leader before single-pod restart: {l} ===", l=leader_before)

            non_leaders = [p for p in pods if p != leader_before]
            if not non_leaders:
                logger.warning(
                    "No non-leader pods found (leader={l}, pods={p}) — will restart any pod",
                    l=leader_before,
                    p=pods,
                )
                non_leaders = pods[:1]

            non_leader_pod = non_leaders[0]

            # Step 1: restart a non-leader pod
            logger.info("--- Step 1: restarting non-leader pod {pod} ---", pod=non_leader_pod)
            non_leader_restart_time = time.time()
            restart_events.append((non_leader_restart_time, "non_leader_restart_event"))
            if not restart_pod_and_wait(non_leader_pod, ns):
                failure_state["failed"] = True

            # Step 2: restart the leader pod (triggers a leader election)
            logger.info("--- Step 2: restarting leader pod {pod} ---", pod=leader_before)
            leader_restart_time = time.time()
            restart_events.append((leader_restart_time, "leader_restart_event"))
            if leader_before and not restart_pod_and_wait(leader_before, ns):
                failure_state["failed"] = True

            leader_after = get_raft_leader()

    logger.info("Waiting for benchmark threads to finish...")
    bench_thread.join()

    # Close the window before measuring it: stop generating load, then let the
    # last aggregate land. Setting agg_stop does not interrupt one already
    # blocked in requests.post, and that is the one most likely to be slow, so
    # reading agg_stats without joining can drop the very sample this gate is
    # for.
    agg_stop.set()
    writes_done.set()
    agg_thread.join(timeout=agg_timeout_s + 5)
    if agg_thread.is_alive():
        logger.warning(
            "Aggregate driver still running after {t:.0f}s; its last sample may be missing",
            t=agg_timeout_s + 5,
        )
    post_snapshot = snapshot_server_metrics(ns)
    logger.info("Benchmark finished.")

    # Display RAFT leader change summary
    if leader_before or leader_after:
        if leader_before == leader_after:
            logger.info(
                "=== RAFT leader unchanged: {l} ===",
                l=leader_before,
            )
        else:
            logger.info(
                "=== RAFT leader changed: {before} -> {after} ===",
                before=leader_before,
                after=leader_after,
            )

    logger.info("Waiting for ingestion threads to finish...")
    for t in ingestion_threads:
        t.join()

    # Annotate every benchmark CSV with one sentinel row per disruption event so the
    # pre/post split is unambiguous in both the validation below and any manual
    # inspection of the artifacts.
    #
    # rolling-restart:       one row  → "rolling_restart_event"
    # single-pod-restart:    two rows → "non_leader_restart_event", "leader_restart_event"
    collection_names = [cfg["name"] for cfg in collections]
    for rt, event_name in restart_events:
        annotate_csvs_with_restart(rt, collection_names, test_start_time, event_name=event_name)

    # Validate QPS stability across all benchmark CSVs produced by this run.
    logger.info("Analyzing benchmark results...")
    csv_files = [
        p
        for p in sorted(glob.glob("benchmark_results_*.csv"))
        if os.path.getmtime(p) >= test_start_time
    ]
    if not csv_files:
        logger.error("No benchmark CSV files found for this run — cannot validate QPS")
        failure_state["failed"] = True
    # Reported separately: a query past the per-request timeout records no latency,
    # so no percentile can see it. This is the production signal -- queries held
    # for seconds -- and a count over the whole window is stable where a
    # per-second percentile over ~20 samples is not.
    # Writes refused while the cluster rolls were a signal in their own right in
    # production: none during a healthy rollout, a steady rate during a bad one.
    # The tolerance is deliberately loose and uncalibrated -- it exists so a
    # vectorizer hiccup does not fail the run, not as a measured bound.
    # What production watched: the server's own query latency histogram and its
    # failed-request count over the restart window. A histogram over every query
    # resolves a stall that a client-side p99 over ~20 samples per second cannot.
    if pre_snapshot is None or post_snapshot is None:
        # Already logged which pods were missing. Judging a partial window would
        # either import a pod's lifetime counters or drop its share of the restart.
        logger.error("Server metrics incomplete, so the server gates cannot judge the window")
        failure_state["failed"] = True
    else:
        server = summarise_window(pre_snapshot, post_snapshot)
        logger.info(
            "Server during the restart window: {q:.0f} queries over {n:.0f} pod(s), p50={p50}, "
            "p99={p99}, server_errors={f:.0f} of {r:.0f} requests ({e:.2%}), user_errors={u:.0f}",
            q=server["queries"] or 0,
            n=server["pods"] or 0,
            p50=f"{server['p50_ms']:.0f}ms" if server["p50_ms"] else "n/a",
            p99=f"{server['p99_ms']:.0f}ms" if server["p99_ms"] else "n/a",
            f=server["server_errors"] or 0,
            r=server["requests"] or 0,
            e=server["error_rate"] or 0,
            u=server["user_errors"] or 0,
        )
        if server["pods_missing"]:
            logger.warning(
                "{n:.0f} pod(s) scraped before the window could not be scraped after it, so "
                "their share of the window is missing from these numbers",
                n=server["pods_missing"],
            )

        max_server_p99_ms = get_env_float("MAX_SERVER_P99_MS", 2000.0)
        max_error_rate = get_env_float("MAX_ERROR_RATE", 0.01)

        if not server["queries"]:
            logger.error(
                "Server metrics empty: no get_graphql queries recorded in the window, so "
                "there is nothing to judge. The benchmark's queries normally populate "
                "queries_durations_ms_bucket; an empty window means they never reached "
                "the server or monitoring is off."
            )
            failure_state["failed"] = True
        else:
            if server["p99_ms"] and server["p99_ms"] > max_server_p99_ms:
                logger.error(
                    "Server latency FAILED: p99 {p:.0f}ms over the restart window, above {m:.0f}ms",
                    p=server["p99_ms"],
                    m=max_server_p99_ms,
                )
                failure_state["failed"] = True
            if server["error_rate"] > max_error_rate:
                logger.error(
                    "Server error rate FAILED: {e:.2%} of requests were server errors "
                    "({f:.0f} of {r:.0f}), above {m:.2%}",
                    e=server["error_rate"],
                    f=server["server_errors"],
                    r=server["requests"],
                    m=max_error_rate,
                )
                failure_state["failed"] = True
            if (
                server["p99_ms"]
                and server["p99_ms"] <= max_server_p99_ms
                and (server["error_rate"] <= max_error_rate)
            ):
                logger.info(
                    "Server latency and error rate PASSED: p99 {p:.0f}ms, errors {e:.2%}",
                    p=server["p99_ms"],
                    e=server["error_rate"],
                )

    # A rate, not a count: the number of writes attempted varies with the window
    # and the pacing, so an absolute tolerance needs re-guessing every time one of
    # those changes -- it was set to 50, tripped at 213, set to 400, tripped at
    # 425. Observed failures were 0.3-0.6% of writes attempted, while production's
    # bad rollout refused writes steadily for minutes.
    max_write_error_rate = get_env_float("MAX_WRITE_ERROR_RATE", 0.02)
    total_write_errors = sum(s["window_errors"] for s in write_stats.values())
    total_attempted = sum(s["window_attempted"] for s in write_stats.values())
    seed_errors = sum(s["errors"] - s["window_errors"] for s in write_stats.values())
    write_error_rate = (total_write_errors / total_attempted) if total_attempted else 0.0
    by_collection = {n: s["window_errors"] for n, s in write_stats.items() if s["window_errors"]}
    if total_write_errors or seed_errors:
        logger.info(
            "Write errors in the restart window: {n} of {a} attempted ({r:.2%}) by "
            "collection: {c}. Before the window: {s}, not gated",
            n=total_write_errors,
            a=total_attempted,
            r=write_error_rate,
            c=by_collection,
            s=seed_errors,
        )
    if not total_attempted:
        # Nothing to divide by: the gate would read 0% and pass whatever happened.
        if sustained_writes:
            logger.error(
                "No writes were attempted inside the restart window, so the write error "
                "rate judges nothing. The benchmark ended before a sustained round began."
            )
            failure_state["failed"] = True
        else:
            logger.info("SUSTAINED_WRITES is off, so there is no write rate to gate")
    elif write_error_rate > max_write_error_rate:
        logger.error(
            "Write validation FAILED: {r:.2%} of writes failed ({n} of {a}), above {m:.2%}",
            r=write_error_rate,
            n=total_write_errors,
            a=total_attempted,
            m=max_write_error_rate,
        )
        failure_state["failed"] = True

    max_slow_aggregates = get_env_int("MAX_SLOW_AGGREGATES", 0)
    logger.info(
        "Aggregates: {n} run, max {m:.0f}ms, {s} over {t:.0f}ms, {f} failed",
        n=agg_stats["count"],
        m=agg_stats["max_ms"],
        s=agg_stats["slow"],
        t=agg_slow_ms,
        f=agg_stats["failed"],
    )
    if not agg_stats["count"]:
        logger.error("No aggregates ran: the aggregate path was never exercised")
        failure_state["failed"] = True
    elif agg_stats["slow"] > max_slow_aggregates:
        logger.error(
            "Aggregate validation FAILED: {s} over {t:.0f}ms (max {m:.0f}ms), tolerance {a}",
            s=agg_stats["slow"],
            t=agg_slow_ms,
            m=agg_stats["max_ms"],
            a=max_slow_aggregates,
        )
        failure_state["failed"] = True
    else:
        logger.info("Aggregate validation PASSED: max {m:.0f}ms", m=agg_stats["max_ms"])

    if agg_stats["failed"]:
        logger.warning(
            "{f} of {n} aggregates were refused. Reported, not gated: a refusal during a "
            "restart is availability, which the server error rate judges as a rate, "
            "whereas an aggregate that hangs is the regression this gate is for.",
            f=agg_stats["failed"],
            n=agg_stats["count"],
        )

    max_timeouts = get_env_int("MAX_QUERY_TIMEOUTS", 0)
    total_timeouts = sum(timeout_counts.values())
    if timeout_counts:
        logger.info(
            "Per-request timeouts (>{t}s) by collection: {c}",
            t=PER_REQUEST_TIMEOUT_S,
            c=timeout_counts,
        )
    if total_timeouts > max_timeouts:
        logger.error(
            "Timeout validation FAILED: {n} query(s) exceeded {t}s, tolerance {m}",
            n=total_timeouts,
            t=PER_REQUEST_TIMEOUT_S,
            m=max_timeouts,
        )
        failure_state["failed"] = True
    else:
        logger.info("Timeout validation PASSED: no query exceeded {t}s", t=PER_REQUEST_TIMEOUT_S)

    # Per-collection client numbers are recorded, not gated. Each CSV row covers
    # ~20 queries, so its "p99" is a maximum, and one collection crossing a
    # threshold has repeatedly meant nothing -- the same collection tripped on
    # both a fixed and an unfixed build in the same run. The cluster-wide server
    # histogram above is what decides, as it did in production.
    for csv_path in csv_files:
        passed, reason = validate_benchmark_csv(
            csv_path,
            target_qps=float(benchmark_qps),
            degraded_ms=get_env_float("DEGRADED_MS", 1000.0),
            max_degraded_fraction=get_env_float("MAX_DEGRADED_FRACTION", 0.15),
        )
        level = logger.info if passed else logger.warning
        level(
            "Client per-collection [{path}] {verdict}: {reason}",
            path=os.path.basename(csv_path),
            verdict="within thresholds" if passed else "outside thresholds",
            reason=reason,
        )

    logger.info("Closing client...")
    client.close()
    if not wait_for_statefulset_ready(ns):
        failure_state["failed"] = True
    logger.info("Done.")

    if failure_state["failed"]:
        logger.error("One or more operations failed. Exiting with non-zero status for CI.")
        sys.exit(1)


if __name__ == "__main__":
    main()
