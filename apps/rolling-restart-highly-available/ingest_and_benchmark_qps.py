import os
import re
import sys
import io
import time
import threading
import asyncio
import subprocess
from contextlib import redirect_stdout
from typing import Any, Dict, List

from loguru import logger

try:
    # Prefer explicit import alias to satisfy request wording
    from weaviate import (
        connect_to_local as weaviate_connect_to_local,
        use_async_with_local as weaviate_use_async_with_local,
    )
except Exception:  # pragma: no cover
    # Fallback to module attr if alias import style is not available
    import weaviate  # type: ignore

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
    # Read env-driven knobs
    objects_per_class = get_env_int("OBJECTS_PER_CLASS", 1000)
    batch_size = get_env_int("BATCH_SIZE", 100)
    collection_prefix = os.getenv("COLLECTION_PREFIX", "rrha_")
    ns = os.getenv("K8S_NAMESPACE", "weaviate")

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
        # {
        #     "name": f"{collection_prefix}no_mt_async_hnsw_rq",
        #     "vector_index": "hnsw_rq",
        #     "multitenant": False,
        #     "async_enabled": True,
        # },
        # {
        #     "name": f"{collection_prefix}mt_sync_hnsw_sq",
        #     "vector_index": "hnsw_sq",
        #     "multitenant": True,
        #     "async_enabled": False,
        # },
        # {
        #     "name": f"{collection_prefix}mt_async_hnsw_bq",
        #     "vector_index": "hnsw_bq",
        #     "multitenant": True,
        #     "async_enabled": True,
        # },
    ]

    # Create collections
    for cfg in collections:
        try:
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
            )
        except Exception as e:  # pragma: no cover
            logger.exception(f"Failed to create collection {cfg['name']}: {e}")

    # Start background ingestion for each collection
    threads: List[threading.Thread] = []

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
                )
            out = buf.getvalue()
            m = re.search(r"Encountered\s+(\d+)\s+total errors", out)
            if m:
                num_errors = int(m.group(1))
                if num_errors > 0:
                    logger.error(
                        "Ingestion for {name} reported {n} errors.",
                        name=name,
                        n=num_errors,
                    )
                    failure_state["failed"] = True
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
        threads.append(t)

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
                    max_duration=180,
                    query_terms=["test"],
                    file_alias=collection_name,
                    fail_on_timeout=True,
                    warmup_duration=0,
                    qps=5,
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

    logger.info("Waiting for 15 seconds...")
    time.sleep(15)

    # Rolling restart
    logger.info("Rolling restart for sts/weaviate...")
    try:
        cmd = [
            "kubectl",
            "rollout",
            "restart",
            "sts/weaviate",
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
            failure_state["failed"] = True
    except Exception as e:  # pragma: no cover
        logger.exception(f"kubectl rollout restart failed: {e}")
        logger.info("Closing client...")
        client.close()
        if not wait_for_statefulset_ready(ns):
            failure_state["failed"] = True

    logger.info("Waiting for benchmark threads to finish...")
    bench_thread.join()
    logger.info("Benchmark finished.")

    logger.info("Analyzing benchmark results...")

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
