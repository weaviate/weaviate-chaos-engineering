"""
Does a rolling restart stall queries when a collection has many tenants?

Many HOT tenants, steady queries and writes across them, then roll the cluster and
compare the queries issued while it rolls against the quiet ones before. Nothing
elaborate is needed to provoke it: when a restarted node finishes catching up it
reconciles tenant status, which walks every local HOT shard, and anything
expensive done there runs inside the serial apply and delays every entry behind
it. Writes to tenants also activate them, which lands on the same path.

Why the restart QPS benchmark cannot see this: it drives a single tenant per
collection, so neither the reconcile nor activation has anything to walk.

Requires lazy shard loading to be ON (LAZY_LOAD_SHARD_COUNT_THRESHOLD=0). With it
off, shards load eagerly by design on every build and there is nothing to tell
apart.
"""

import os
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from loguru import logger

import weaviate
from weaviate.classes.config import DataType, Property
from weaviate.classes.tenants import Tenant

from gates import count_deadline_waits, evaluate, get_env_float, get_env_int

CLASS = os.getenv("CLASS_NAME", "MtRolloutLatency")
NAMESPACE = os.getenv("K8S_NAMESPACE", "weaviate")
TENANTS = get_env_int("TENANTS", 400)
SEED_WORKERS = get_env_int("SEED_WORKERS", 16)
QUERY_INTERVAL_S = get_env_float("QUERY_INTERVAL_S", 0.2)
WRITE_INTERVAL_S = get_env_float("WRITE_INTERVAL_S", 0.1)
BASELINE_S = get_env_float("BASELINE_S", 20)
SETTLE_S = get_env_float("SETTLE_S", 15)
# Percentiles, not extremes: the client's port-forward breaks during the roll and
# one reconnect costs seconds on a healthy build. Measured p99s were 11,218/12,134ms
# before the fix against 12.8/34.8ms after, and writes 7,585/1,354ms against
# 159/164ms, so both limits sit roughly an order of magnitude clear of each side.
MAX_QUERY_P99_MS = get_env_float("MAX_QUERY_P99_MS", 1000)
MAX_WRITE_P99_MS = get_env_float("MAX_WRITE_P99_MS", 500)
MIN_QUERIES = get_env_int("MIN_QUERIES", 30)
ROLLOUT_TIMEOUT_S = get_env_int("ROLLOUT_TIMEOUT_S", 900)

VEC = [0.1] * 8
# (monotonic seconds, milliseconds) so a sample can be attributed to a window
Sample = Tuple[float, float]


def _no_vectorizer(kwargs: dict) -> dict:
    """Vector config moved between client versions; try the current spelling first."""
    from weaviate.classes.config import Configure

    if hasattr(Configure, "Vectors"):
        kwargs["vector_config"] = Configure.Vectors.self_provided()
    else:
        kwargs["vectorizer_config"] = Configure.Vectorizer.none()
    return kwargs


def pod_names() -> List[str]:
    out = subprocess.run(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            NAMESPACE,
            "-o",
            'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{end}',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return [p for p in out.stdout.split() if p.startswith("weaviate-") and p[-1].isdigit()]


def scrape_pods() -> Dict[str, str]:
    """Each pod's metrics text, keyed by pod, for a reset-aware per-pod delta.

    Served through the API proxy because the image carries no curl.
    """
    texts = {}
    for pod in pod_names():
        out = subprocess.run(
            [
                "kubectl",
                "get",
                "--raw",
                f"/api/v1/namespaces/{NAMESPACE}/pods/{pod}:2112/proxy/metrics",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0:
            texts[pod] = out.stdout
        else:
            logger.warning("could not scrape {p}: {e}", p=pod, e=out.stderr[:160])
    return texts


def deadline_waits(before: Dict[str, str], after: Dict[str, str]) -> Optional[int]:
    return count_deadline_waits(before, after)


def rolling_restart() -> Tuple[float, float]:
    """Restart every pod in turn; returns the window it occupied."""
    began = time.monotonic()
    for args in (
        ["rollout", "restart", "sts/weaviate"],
        ["rollout", "status", "sts/weaviate", f"--timeout={ROLLOUT_TIMEOUT_S}s"],
    ):
        out = subprocess.run(
            ["kubectl", *args, "-n", NAMESPACE], capture_output=True, text=True, check=False
        )
        logger.info(
            "kubectl {a}: rc={rc} {o}",
            a=args[1],
            rc=out.returncode,
            o=(out.stdout or out.stderr).strip()[:200],
        )
    return began, time.monotonic()


def within(samples: List[Sample], lo: float, hi: float) -> List[float]:
    return [ms for at, ms in samples if lo <= at <= hi]


def main() -> int:
    client = weaviate.connect_to_local(
        host=os.getenv("WEAVIATE_HOST", "localhost"),
        port=get_env_int("WEAVIATE_PORT", 8080),
        grpc_port=get_env_int("WEAVIATE_GRPC_PORT", 50051),
    )
    try:
        from weaviate.classes.config import Configure

        if client.collections.exists(CLASS):
            client.collections.delete(CLASS)
        client.collections.create(
            name=CLASS,
            multi_tenancy_config=Configure.multi_tenancy(
                enabled=True, auto_tenant_creation=True, auto_tenant_activation=True
            ),
            properties=[Property(name="body", data_type=DataType.TEXT)],
            **_no_vectorizer({}),
        )
        col = client.collections.get(CLASS)

        tenants = [f"tenant-{i}" for i in range(TENANTS)]
        logger.info("creating {n} tenants", n=len(tenants))
        col.tenants.create([Tenant(name=t) for t in tenants])

        logger.info("seeding one object per tenant with {w} workers", w=SEED_WORKERS)
        seeded = time.monotonic()
        with ThreadPoolExecutor(max_workers=SEED_WORKERS) as pool:
            list(
                pool.map(
                    lambda t: col.with_tenant(t).data.insert({"body": "seed"}, vector=VEC), tenants
                )
            )
        logger.info("seeded in {s:.1f}s", s=time.monotonic() - seeded)

        queries: List[Sample] = []
        writes: List[Sample] = []
        stop = threading.Event()

        def loop(samples: List[Sample], interval: float, do: callable, what: str) -> None:
            rng = random.Random(len(what))
            while not stop.is_set():
                tenant = tenants[rng.randrange(len(tenants))]
                began = time.monotonic()
                try:
                    do(tenant)
                except Exception as e:
                    # a refusal is still time the caller waited for
                    logger.warning("{w} on {t} failed: {e}", w=what, t=tenant, e=e)
                samples.append((began, (time.monotonic() - began) * 1000))
                stop.wait(interval)

        threads = [
            threading.Thread(
                target=loop,
                name="query",
                daemon=True,
                args=(
                    queries,
                    QUERY_INTERVAL_S,
                    lambda t: col.with_tenant(t).aggregate.over_all(total_count=True),
                    "query",
                ),
            ),
            threading.Thread(
                target=loop,
                name="write",
                daemon=True,
                args=(
                    writes,
                    WRITE_INTERVAL_S,
                    lambda t: col.with_tenant(t).data.insert({"body": "load"}, vector=VEC),
                    "write",
                ),
            ),
        ]
        for t in threads:
            t.start()

        logger.info("{s:.0f}s of quiet load for a baseline", s=BASELINE_S)
        time.sleep(BASELINE_S)
        quiet_end = time.monotonic()
        before_texts = scrape_pods()

        logger.info("rolling the cluster with load running")
        roll_began, roll_ended = rolling_restart()
        # the apply backlog outlives the restart that caused it
        time.sleep(SETTLE_S)
        roll_window_end = time.monotonic()

        stop.set()
        for t in threads:
            t.join(timeout=15)

        passed, lines = evaluate(
            within(queries, 0, quiet_end),
            within(queries, roll_began, roll_window_end),
            [ms for _, ms in writes],
            deadline_waits(before_texts, scrape_pods()),
            MAX_QUERY_P99_MS,
            MAX_WRITE_P99_MS,
            MIN_QUERIES,
        )
        logger.info("rollout took {s:.0f}s", s=roll_ended - roll_began)
        for line in lines:
            (logger.error if "FAILED" in line else logger.info)(line)
        return 0 if passed else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
