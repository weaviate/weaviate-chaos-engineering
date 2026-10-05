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
from contextlib import redirect_stdout
from typing import Any, Dict, List, Optional, Tuple

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

from validation import (
    annotate_csvs_with_restart,
    get_env_float,
    get_env_int,
    validate_benchmark_csv,
)

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
                shards=(
                    0 if cfg["multitenant"] else 4
                ),  # Ensure we have as many shards as replicas only for MT collections
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
    write_errors: Dict[str, int] = {}
    writes_done = threading.Event()

    def ingest_round(name: str, auto_tenants: int, limit: int) -> int:
        """One pass of create_data; returns the error count it reported."""
        buf = io.StringIO()
        with redirect_stdout(buf):
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
        errors = 0
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
            try:
                errors += ingest_round(name, auto_tenants, limit)
                rounds += 1
            except Exception as e:  # pragma: no cover
                # A write refused mid-restart is the thing being measured, not a
                # reason to stop writing.
                logger.warning("Ingestion round for {name} failed: {e}", name=name, e=e)
                errors += 1
            if not sustained_writes or writes_done.is_set():
                break
            if writes_done.wait(timeout=sustained_write_pause_s):
                break

        write_errors[name] = errors
        logger.info(
            "Ingestion for {name} finished: {r} round(s), {e} error(s)",
            name=name,
            r=rounds,
            e=errors,
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

    logger.info("Ingestion started for all collections. Waiting for 30 seconds...")
    time.sleep(30)

    # Start QPS benchmarks for all collections in parallel (daemon thread, non-blocking)
    logger.info("Starting QPS benchmarks for all collections...")

    timeout_counts: Dict[str, int] = {}

    async def run_all_benchmarks() -> None:
        async def benchmark_one(collection_name: str) -> None:
            async_client = weaviate_use_async_with_local()
            manager = BenchmarkQPSManager(async_client)
            buf = io.StringIO()
            with redirect_stdout(buf):
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
    # let the writers stop once there is nothing left to measure
    writes_done.set()
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
    max_write_errors = get_env_int("MAX_WRITE_ERRORS", 50)
    total_write_errors = sum(write_errors.values())
    if write_errors:
        logger.info("Write errors by collection: {c}", c=write_errors)
    if total_write_errors > max_write_errors:
        logger.error(
            "Write validation FAILED: {n} write error(s) across the run, tolerance {m}",
            n=total_write_errors,
            m=max_write_errors,
        )
        failure_state["failed"] = True

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

    for csv_path in csv_files:
        passed, reason = validate_benchmark_csv(
            csv_path,
            target_qps=float(benchmark_qps),
            degraded_ms=get_env_float("DEGRADED_MS", 1000.0),
            max_degraded_fraction=get_env_float("MAX_DEGRADED_FRACTION", 0.15),
        )
        if passed:
            logger.info("QPS validation PASSED [{path}]: {reason}", path=csv_path, reason=reason)
        else:
            logger.error("QPS validation FAILED [{path}]: {reason}", path=csv_path, reason=reason)
            failure_state["failed"] = True

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
