"""
Does activating tenants stall unrelated queries?

Shape of the test: a handful of probe tenants stay HOT and are queried throughout.
A much larger set is driven COLD and then woken up in a burst by writing to them,
which is what auto-tenant activation does in production. The probe queries are the
victims -- they ask for nothing the churn touches, so any latency they pick up came
from the cluster, not from their own work.

Why it catches what the restart benchmark cannot: that one drives a single tenant
per collection and never moves one from COLD to HOT, so the activation path is
never exercised at all.

Requires lazy shard loading to be ON (LAZY_LOAD_SHARD_COUNT_THRESHOLD=0). With it
off, activation loads eagerly by design on every build and there is nothing to tell
apart.
"""

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from loguru import logger

import weaviate
from weaviate.classes.config import DataType, Property
from weaviate.classes.tenants import Tenant

from gates import evaluate, get_env_float, get_env_int
from server_metrics import snapshot_pods, summarise_window

CLASS = os.getenv("CLASS_NAME", "MtActivationLatency")
NAMESPACE = os.getenv("K8S_NAMESPACE", "weaviate")
PROBE_TENANTS = get_env_int("PROBE_TENANTS", 4)
CHURN_TENANTS = get_env_int("CHURN_TENANTS", 400)
ACTIVATION_WORKERS = get_env_int("ACTIVATION_WORKERS", 16)
QUERY_INTERVAL_S = get_env_float("QUERY_INTERVAL_S", 0.2)
MAX_QUERY_MS = get_env_float("MAX_QUERY_MS", 5000)
MIN_QUERIES = get_env_int("MIN_QUERIES", 50)
DO_ROLLOUT = os.getenv("DO_ROLLOUT", "true").strip().lower() == "true"
ROLLOUT_TIMEOUT_S = get_env_int("ROLLOUT_TIMEOUT_S", 900)


def _cold_status():
    """The client has renamed these; accept whichever this version ships."""
    from weaviate.classes.tenants import TenantActivityStatus as S

    for name in ("INACTIVE", "COLD"):
        if hasattr(S, name):
            return getattr(S, name)
    raise RuntimeError("no inactive tenant status in this client")


def _no_vectorizer(kwargs: dict) -> dict:
    """Vector config moved between client versions; try the current spelling first."""
    try:
        from weaviate.classes.config import Configure

        if hasattr(Configure, "Vectors"):
            kwargs["vector_config"] = Configure.Vectors.self_provided()
        else:
            kwargs["vectorizer_config"] = Configure.Vectorizer.none()
    except Exception as e:  # pragma: no cover
        logger.warning("could not set a vector config, relying on defaults: {e}", e=e)
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


def deadline_waits() -> Optional[int]:
    """
    Schema-version waits that ran out of time, summed over pods.

    Served through the API proxy because the image carries no curl. None when the
    build does not expose the metric, which the gate treats as "cannot judge".
    """
    total, seen = 0, False
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
        if out.returncode != 0:
            logger.warning("could not scrape {pod}: {e}", pod=pod, e=out.stderr[:160])
            continue
        for line in out.stdout.splitlines():
            if (
                line.startswith("weaviate_cluster_store_wait_for_index_duration_seconds_count")
                and 'outcome="deadline"' in line
            ):
                seen = True
                total += int(float(line.rsplit(" ", 1)[1]))
    return total if seen else None


def scrape_pods() -> dict:
    """Each pod's metrics text, keyed by pod, for a reset-aware per-pod delta."""
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
    return texts


def rolling_restart() -> None:
    """
    Restart every pod in turn. This is the only way to reach the tenant reconcile
    that runs on the DB reload after a node finishes catching up, which walks every
    local HOT shard -- the activation burst alone never gets there.
    """
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

        probes = [f"probe-{i}" for i in range(PROBE_TENANTS)]
        churn = [f"churn-{i}" for i in range(CHURN_TENANTS)]
        logger.info("creating {p} probe and {c} churn tenants", p=len(probes), c=len(churn))
        col.tenants.create([Tenant(name=n) for n in probes + churn])

        vec = [0.1] * 8
        for n in probes + churn:
            col.with_tenant(n).data.insert({"body": "seed"}, vector=vec)

        cold = _cold_status()
        logger.info("deactivating {c} churn tenants", c=len(churn))
        col.tenants.update([Tenant(name=n, activity_status=cold) for n in churn])

        query_ms: List[float] = []
        activation_ms: List[float] = []
        stop = threading.Event()

        def probe_queries() -> None:
            i = 0
            while not stop.is_set():
                tenant = probes[i % len(probes)]
                began = time.perf_counter()
                try:
                    col.with_tenant(tenant).aggregate.over_all(total_count=True)
                except Exception as e:
                    # a refusal is still time the caller waited
                    logger.warning("probe query on {t} failed: {e}", t=tenant, e=e)
                query_ms.append((time.perf_counter() - began) * 1000)
                i += 1
                stop.wait(QUERY_INTERVAL_S)

        prober = threading.Thread(target=probe_queries, name="probe", daemon=True)
        prober.start()
        # let the probe establish a baseline before the burst
        time.sleep(5)

        def wake(tenant: str) -> None:
            began = time.perf_counter()
            try:
                col.with_tenant(tenant).data.insert({"body": "wake"}, vector=vec)
            except Exception as e:
                logger.warning("waking {t} failed: {e}", t=tenant, e=e)
            activation_ms.append((time.perf_counter() - began) * 1000)

        logger.info("waking {c} tenants with {w} workers", c=len(churn), w=ACTIVATION_WORKERS)
        burst_began = time.perf_counter()
        with ThreadPoolExecutor(max_workers=ACTIVATION_WORKERS) as pool:
            list(pool.map(wake, churn))
        logger.info("burst finished in {s:.1f}s", s=time.perf_counter() - burst_began)

        # keep querying briefly: the apply backlog outlives the writes that caused it
        time.sleep(10)
        stop.set()
        prober.join(timeout=10)

        passed, lines = evaluate(
            query_ms, activation_ms, deadline_waits(), MAX_QUERY_MS, MIN_QUERIES
        )
        for line in lines:
            (logger.error if "FAILED" in line else logger.info)(line)
        return 0 if passed else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
