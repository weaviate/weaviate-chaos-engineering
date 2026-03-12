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
from weaviate_cli.managers.benchmark_manager import BenchmarkQPSManager


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(f"Invalid int for {name}={value!r}; using default {default}")
        return default


def annotate_csvs_with_restart(
    restart_time: float,
    collection_names: List[str],
    test_start_time: float,
    event_name: str = "restart_event",
) -> None:
    """
    Insert a sentinel row with phase_name=*event_name* into each collection's benchmark
    CSV at the position corresponding to restart_time.  The sentinel makes it trivial for
    the validation function to split pre-restart baseline from the restart window.

    For a rolling restart there is a single sentinel ("rolling_restart_event").
    For a single-pod restart two sentinels are inserted per call:
        "non_leader_restart_event"  – when the non-leader pod is deleted
        "leader_restart_event"      – when the leader pod is deleted

    The row looks like:
        <restart_time>,<event_name>,,,,,,0
    with empty latency columns so consumers that expect floats can skip it.
    """
    for name in collection_names:
        # Pick the CSV written during this test run (newest file for this collection)
        pattern = f"benchmark_results_{name}_*.csv"
        candidates = [
            p for p in sorted(glob.glob(pattern)) if os.path.getmtime(p) >= test_start_time
        ]
        if not candidates:
            logger.warning(f"No benchmark CSV found for collection {name}, skipping annotation")
            continue
        csv_path = candidates[-1]

        with open(csv_path, newline="") as f:
            reader = csv_module.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)

        if not rows or not fieldnames:
            logger.warning(f"CSV {csv_path} is empty, skipping annotation")
            continue

        # Find the insertion point: first row whose timestamp >= restart_time
        insert_at = len(rows)
        for i, row in enumerate(rows):
            try:
                if float(row["timestamp"]) >= restart_time:
                    insert_at = i
                    break
            except (ValueError, KeyError):
                continue

        sentinel: Dict[str, str] = {k: "" for k in fieldnames}
        sentinel["timestamp"] = f"{restart_time:.6f}"
        sentinel["phase_name"] = event_name
        sentinel["actual_qps"] = "0"
        sentinel["total_queries"] = "0"

        rows.insert(insert_at, sentinel)

        with open(csv_path, "w", newline="") as f:
            writer = csv_module.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        logger.info(
            "Annotated {path} with {event} at row {pos} (t={ts:.1f})",
            path=csv_path,
            event=event_name,
            pos=insert_at + 1,
            ts=restart_time,
        )


def validate_benchmark_csv(
    csv_path: str,
    target_qps: float,
    max_drop_ratio: float = 0.6,
    sustained_window: int = 3,
    baseline_skip_rows: int = 3,
) -> Tuple[bool, str]:
    """
    Validate a (restart-annotated) benchmark CSV for QPS regressions.

    Strategy
    --------
    1. Split the CSV at the 'restart_event' sentinel into a baseline section
       (pre-restart) and a restart window (post-restart).
    2. Compute the stable baseline QPS from the pre-restart rows, skipping the
       first ``baseline_skip_rows`` rows which may still reflect EWMA warmup.
    3. Derive a failure threshold: baseline_qps * (1 - max_drop_ratio).
       Default is 60 %, e.g. threshold = 8 QPS when baseline = 20 QPS.
    4. Scan every consecutive window of ``sustained_window`` rows in the
       post-restart section. If ALL rows in a window are below the threshold,
       the test fails.  Requiring multiple consecutive seconds prevents
       single-row EWMA noise from triggering false positives.

    Thresholds are intentionally generous (60 % drop, 3-second window) so that
    normal pod-restart overhead (~5-15 % dip for < 2 s) never causes a flake,
    while a real regression (90 %+ drop sustained for 6+ s) is unmistakable.

    Args:
        csv_path:          Path to the annotated benchmark CSV.
        target_qps:        Configured QPS target; used as fallback baseline when
                           there are too few pre-restart rows.
        max_drop_ratio:    Maximum tolerated QPS drop fraction (0.6 = 60 %).
        sustained_window:  Consecutive rows all below threshold that trigger fail.
        baseline_skip_rows: Leading pre-restart rows to skip (EWMA settling).

    Returns:
        (passed: bool, reason: str)
    """
    with open(csv_path, newline="") as f:
        reader = csv_module.DictReader(f)
        rows = list(reader)

    if len(rows) < 10:
        return False, f"Too few rows ({len(rows)}) to validate"

    # Locate the first restart sentinel (any phase_name ending in "_restart_event"
    # or equal to "restart_event" for backward compatibility).
    def _is_restart_sentinel(row: Dict[str, str]) -> bool:
        name = row.get("phase_name", "")
        return name == "restart_event" or name.endswith("_restart_event")

    restart_idx: Optional[int] = None
    for i, row in enumerate(rows):
        if _is_restart_sentinel(row):
            restart_idx = i
            break

    if restart_idx is None:
        return False, "No restart_event sentinel found — annotation may have failed"

    # --- Baseline ---
    pre_rows = [r for r in rows[:restart_idx] if not _is_restart_sentinel(r)]
    stable_pre = pre_rows[baseline_skip_rows:]

    if len(stable_pre) < 3:
        baseline_qps = target_qps
        logger.warning(
            "Only {n} stable pre-restart rows available; using target_qps={t} as baseline",
            n=len(stable_pre),
            t=target_qps,
        )
    else:
        samples = []
        for r in stable_pre:
            try:
                samples.append(float(r["actual_qps"]))
            except (ValueError, KeyError):
                pass
        baseline_qps = sum(samples) / len(samples) if samples else target_qps

    threshold_qps = baseline_qps * (1.0 - max_drop_ratio)

    # --- Post-restart window ---
    # Skip any additional restart sentinels (e.g. the leader_restart_event that follows
    # non_leader_restart_event in the single-pod-restart variant) so they don't
    # pollute the QPS sample list.
    post_rows = [
        r
        for r in rows[restart_idx + 1 :]
        if not _is_restart_sentinel(r) and r.get("actual_qps") not in ("", "0")
    ]

    if not post_rows:
        return False, "No post-restart data rows found"

    qps_values: List[float] = []
    for r in post_rows:
        try:
            qps_values.append(float(r["actual_qps"]))
        except (ValueError, KeyError):
            pass

    if not qps_values:
        return False, "Could not parse any actual_qps values in post-restart data"

    # Sliding-window sustained-drop check
    for i in range(max(1, len(qps_values) - sustained_window + 1)):
        window = qps_values[i : i + sustained_window]
        if len(window) < sustained_window:
            break
        if all(v < threshold_qps for v in window):
            return (
                False,
                f"QPS sustained below threshold for {sustained_window}+ consecutive seconds "
                f"starting at post-restart row {i + 1}: "
                f"window={[round(v, 2) for v in window]}, "
                f"threshold={threshold_qps:.2f} "
                f"(baseline={baseline_qps:.2f}, max_drop={max_drop_ratio * 100:.0f}%)",
            )

    min_observed = min(qps_values)
    return (
        True,
        f"OK — baseline={baseline_qps:.2f} QPS, "
        f"min_observed={min_observed:.2f} QPS, "
        f"threshold={threshold_qps:.2f} QPS ({max_drop_ratio * 100:.0f}% drop allowed)",
    )


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
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            ns,
            "-o",
            "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
        ],
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
        ["kubectl", "delete", "pod", pod_name, "-n", ns],
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
        ["kubectl", "wait", f"pod/{pod_name}", "--for=delete", "--timeout=60s", "-n", ns],
        capture_output=True,
        text=True,
        check=False,
    )

    wait_result = subprocess.run(
        [
            "kubectl",
            "wait",
            f"pod/{pod_name}",
            "--for=condition=Ready",
            f"--timeout={timeout_sec}s",
            "-n",
            ns,
        ],
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
    cmd = [
        "kubectl",
        "rollout",
        "status",
        "sts/weaviate",
        f"--timeout={timeout_sec}s",
    ]
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
    # Allow a small number of vectorizer 500s during ingestion without failing the
    # whole job.  A handful of failed objects (< 0.1 % at 50k) is normal noise from
    # model2vec; a large count indicates a real problem.
    ingestion_max_errors = get_env_int("INGESTION_MAX_ERRORS", 10)

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

    def ingest_target(name: str, is_mt: bool) -> None:
        try:
            # For MT collections, ingest into a single tenant using autoTenantCreation
            auto_tenants = 1 if is_mt else 0
            logger.info(
                "Starting ingestion for {name} (limit={limit}, tenants={auto_tenants})",
                name=name,
                limit=objects_per_class,
                auto_tenants=auto_tenants,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                data_manager.create_data(
                    collection=name,
                    limit=objects_per_class,
                    consistency_level="quorum",
                    randomize=True,
                    auto_tenants=auto_tenants,
                    batch_size=batch_size,
                    wait_for_indexing=True,
                )
            out = buf.getvalue()
            m = re.search(r"Encountered\s+(\d+)\s+total errors", out)
            if m:
                num_errors = int(m.group(1))
                if num_errors > ingestion_max_errors:
                    logger.error(
                        "Ingestion for {name} reported {n} errors (exceeds tolerance of {tol}).",
                        name=name,
                        n=num_errors,
                        tol=ingestion_max_errors,
                    )
                    failure_state["failed"] = True
                elif num_errors > 0:
                    logger.warning(
                        "Ingestion for {name} reported {n} error(s) — within tolerance of {tol}, continuing.",
                        name=name,
                        n=num_errors,
                        tol=ingestion_max_errors,
                    )
        except Exception as e:  # pragma: no cover
            logger.exception(f"Ingestion error for {name}: {e}")
            failure_state["failed"] = True

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

    async def run_all_benchmarks() -> None:
        async def benchmark_one(collection_name: str) -> None:
            async_client = weaviate_use_async_with_local()
            manager = BenchmarkQPSManager(async_client)
            buf = io.StringIO()
            with redirect_stdout(buf):
                await manager.run_benchmark(
                    collection=collection_name,
                    max_duration=120,
                    query_type="hybrid",
                    query_terms=["test"],
                    file_alias=collection_name,
                    fail_on_timeout=True,
                    warmup_duration=0,
                    qps=benchmark_qps,
                    output="csv",
                    generate_graph=True,
                )
            out = buf.getvalue()
            if (
                "No successful queries were completed" in out
                or "timed out after" in out
                or "generated an exception" in out
            ):
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

    logger.info("Waiting for 15 seconds before triggering disruption...")
    time.sleep(15)

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
        cmd = ["kubectl", "rollout", "restart", "sts/weaviate"]
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
    for csv_path in csv_files:
        passed, reason = validate_benchmark_csv(csv_path, target_qps=float(benchmark_qps))
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
