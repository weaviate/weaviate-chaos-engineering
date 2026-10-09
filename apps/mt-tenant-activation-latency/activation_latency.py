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

from gates import DeadlineAccumulator, evaluate, get_env_float, get_env_int

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
# Writes run the whole time at WRITE_INTERVAL_S, so hundreds are expected; this
# only catches a write worker that never really got going, whose p99 would be
# whatever its handful of samples happened to be.
MIN_WRITES = get_env_int("MIN_WRITES", 30)
ROLLOUT_TIMEOUT_S = get_env_int("ROLLOUT_TIMEOUT_S", 900)
# Deliberately wide: the roll costs the client its port-forward, so a few
# refusals are expected on any build and this only catches a workload that spent
# the window erroring instead of being served. See gates.evaluate.
MAX_FAILURE_RATIO = get_env_float("MAX_FAILURE_RATIO", 0.5)
# A failed call that waited this long was not a refusal. 20s sits above the worst
# reconnect artifact measured on a healthy build (15,015ms) and below the
# client's query timeouts, so a request that timed out lands here and a reconnect
# does not.
STALL_MS = get_env_float("STALL_MS", 20_000)
# A couple, so one pathological reconnect cannot fail a run on its own, while the
# handful of timeouts a real stall produces does.
MAX_STALLED_CALLS = get_env_int("MAX_STALLED_CALLS", 2)
# These counters reset with the process, so the window has to be sampled while it
# is open rather than differenced across the roll. See gates.DeadlineAccumulator.
SCRAPE_INTERVAL_S = get_env_float("SCRAPE_INTERVAL_S", 5)
# Longer than the worst single request seen on a healthy build (a ~15s
# port-forward reconnect), so a worker still blocked after this is a stall, not
# an artifact.
WORKER_DRAIN_S = get_env_float("WORKER_DRAIN_S", 60)

VEC = [0.1] * 8
# (monotonic seconds, milliseconds, whether the call was served) so a sample can
# be attributed to a window and refusals kept out of the latency distribution
Sample = Tuple[float, float, bool]


def _no_vectorizer(kwargs: dict) -> dict:
    """Vector config moved between client versions; try the current spelling first."""
    from weaviate.classes.config import Configure

    if hasattr(Configure, "Vectors"):
        kwargs["vector_config"] = Configure.Vectors.self_provided()
    else:
        kwargs["vectorizer_config"] = Configure.Vectorizer.none()
    return kwargs


def pod_names() -> List[str]:
    """Every weaviate pod, or a raise.

    An unanswered listing is not an empty cluster: returning [] would scrape
    nobody, and nobody scraped reads as a build that does not expose the metric,
    which lets the deadline gate stand down on a run where it measured nothing.
    """
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
    if out.returncode != 0:
        raise RuntimeError(f"kubectl get pods exited {out.returncode}: {out.stderr.strip()[:200]}")
    return [p for p in out.stdout.split() if p.startswith("weaviate-") and p[-1].isdigit()]


def scrape_pods() -> Dict[str, str]:
    """Each pod's metrics text, keyed by pod, for a reset-aware per-pod delta.

    Served through the API proxy because the image carries no curl.

    Best effort by design: a pod being restarted cannot be scraped and the
    sampler needs whoever answers rather than all-or-nothing. Use
    scrape_every_pod where the scrape has to be complete.
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


def scrape_every_pod(attempts: int = 5, pause_s: float = 3.0) -> Dict[str, str]:
    """A scrape covering every weaviate pod, or a raise.

    For the two scrapes that have to be complete, the accumulator's baseline and
    the final one. A pod missing from the baseline looks newly started, so its
    lifetime deadline count would be charged to the window; a pod missing from
    the last scrape drops whatever it accrued since its previous sample, which
    reads as fewer deadlines than really happened. Retry rather than judge
    either. The samples in between are best effort -- see scrape_pods.
    """
    expected: List[str] = []
    texts: Dict[str, str] = {}
    why = "all (no pods found)"
    for attempt in range(attempts):
        try:
            expected = pod_names()
            texts = scrape_pods()
            if expected and set(expected) <= set(texts):
                return texts
            why = str(sorted(set(expected) - set(texts)) or "all (no pods found)")
        except Exception as e:
            # A listing that itself failed is worth another attempt rather than
            # the run: the API server is also being talked to by the roll.
            why = str(e)
        if attempt < attempts - 1:
            time.sleep(pause_s)
    raise RuntimeError(f"incomplete after {attempts} attempts: {why}")


def rolling_restart(problems: List[str]) -> Tuple[float, float]:
    """Restart every pod in turn; returns the window it occupied.

    Either command failing has to fail the run. A rejected restart or a rollout
    that never reports ready leaves a cluster that was never rolled, and the
    settle period then supplies a window full of fast samples that pass every
    latency gate.
    """
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
        if out.returncode != 0:
            problems.append(
                f"kubectl rollout {args[1]} exited {out.returncode}:"
                f" {(out.stderr or out.stdout).strip()[:200]}"
            )
    return began, time.monotonic()


def within(samples: List[Sample], lo: float, hi: float) -> Tuple[List[float], List[float]]:
    """What the window's calls cost, split by whether the cluster served them.

    The failures keep their durations rather than being counted: a refusal that
    came back instantly and a request that waited out a 30s timeout are both
    failures, and only the duration tells them apart. See gates.evaluate.
    """
    served = [ms for at, ms, ok in samples if ok and lo <= at <= hi]
    failed = [ms for at, ms, ok in samples if not ok and lo <= at <= hi]
    return served, failed


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
            replication_config=Configure.replication(factor=3),
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
        # Anything that went wrong collecting the measurement, rather than in the
        # measurement itself. Appended to from the worker threads, which is safe
        # for a list and keeps the verdict in one place: gates.evaluate fails the
        # run on any of them.
        problems: List[str] = []

        def loop(samples: List[Sample], interval: float, do: callable, what: str) -> None:
            rng = random.Random(len(what))
            while not stop.is_set():
                tenant = tenants[rng.randrange(len(tenants))]
                began = time.monotonic()
                ok = True
                try:
                    do(tenant)
                except Exception as e:
                    # The wait is kept either way; only `ok` decides whether it
                    # counts as a request the cluster served. A refusal that came
                    # back instantly must not fill min_queries, and a 30s timeout
                    # must not be dropped -- it is the stall itself. gates.evaluate
                    # tells the two apart by how long they waited.
                    ok = False
                    logger.warning("{w} on {t} failed: {e}", w=what, t=tenant, e=e)
                samples.append((began, (time.monotonic() - began) * 1000, ok))
                stop.wait(interval)

        def worker(samples: List[Sample], interval: float, do: callable, what: str) -> None:
            """loop, with its death made visible.

            An exception in a thread target reaches threading.excepthook and dies
            there: join() does not re-raise it. Without this a dead worker shows
            up only as fewer samples, and fewer samples is what a percentile gate
            reads as a clean run.
            """
            try:
                loop(samples, interval, do, what)
            except Exception as e:
                problems.append(f"the {what} worker died, so its window is partial: {e}")
                logger.error("the {w} worker died: {e}", w=what, e=e)

        threads = [
            threading.Thread(
                target=worker,
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
                target=worker,
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

        accumulator: Optional[DeadlineAccumulator] = None
        try:
            accumulator = DeadlineAccumulator(scrape_every_pod())
        except Exception as e:
            problems.append(f"could not baseline the deadline counters on every pod: {e}")
            logger.error("could not baseline the deadline counters: {e}", e=e)

        scrape_stop = threading.Event()

        def deadline_sampler() -> None:
            try:
                while not scrape_stop.is_set():
                    try:
                        # Mid-roll a pod, or the API server itself, is legitimately
                        # unreachable. Take whoever answers and keep going: the
                        # accumulator resumes a pod on its next sample, so one
                        # missed observation costs at most one interval, and the
                        # complete scrape at the end is what catches a kubectl
                        # that stayed broken.
                        accumulator.observe(scrape_pods())
                    except Exception as e:
                        logger.warning("deadline sample skipped: {e}", e=e)
                    scrape_stop.wait(SCRAPE_INTERVAL_S)
            except Exception as e:
                problems.append(f"the deadline sampler died, so the window is partial: {e}")
                logger.error("the deadline sampler died: {e}", e=e)

        sampler: Optional[threading.Thread] = None
        if accumulator is not None:
            sampler = threading.Thread(target=deadline_sampler, name="deadlines", daemon=True)
            sampler.start()

        logger.info("rolling the cluster with load running")
        roll_began, roll_ended = rolling_restart(problems)
        # the apply backlog outlives the restart that caused it
        time.sleep(SETTLE_S)
        roll_window_end = time.monotonic()

        # Stop the load before the sampler: a worker can still be inside a call
        # here, and the waits that call is stuck behind belong in the window.
        stop.set()
        for t in threads:
            t.join(timeout=WORKER_DRAIN_S)
            if t.is_alive():
                # Samples are appended once a call returns, so a worker still
                # inside one holds the slowest sample of the run. Evaluating
                # without it would pass on the fast ones that came before.
                problems.append(
                    f"the {t.name} worker was still waiting on a request"
                    f" {WORKER_DRAIN_S:.0f}s after being told to stop, so its"
                    " slowest sample never reached the gate"
                )

        if sampler is not None:
            scrape_stop.set()
            sampler.join(timeout=SCRAPE_INTERVAL_S + 30)
            if sampler.is_alive():
                # It still holds the accumulator, so reading the total here would
                # race a half-folded observation against the final scrape.
                problems.append(
                    "the deadline sampler was still mid-scrape after being told to"
                    " stop, so the deadline total is not a complete window"
                )

        deadline_waits: Optional[int] = None
        if accumulator is not None:
            try:
                # The final scrape has to be complete, unlike the sampler's.
                accumulator.observe(scrape_every_pod())
                deadline_waits = accumulator.total()
            except Exception as e:
                problems.append(f"could not complete the final deadline scrape: {e}")
                logger.error("could not complete the final deadline scrape: {e}", e=e)

        quiet_ms, quiet_failed_ms = within(queries, 0, quiet_end)
        rolling_ms, rolling_failed_ms = within(queries, roll_began, roll_window_end)
        write_ms, write_failed_ms = within(writes, 0, roll_window_end)
        passed, lines = evaluate(
            quiet_ms,
            rolling_ms,
            write_ms,
            deadline_waits,
            MAX_QUERY_P99_MS,
            MAX_WRITE_P99_MS,
            MIN_QUERIES,
            min_writes=MIN_WRITES,
            max_failure_ratio=MAX_FAILURE_RATIO,
            max_stalled_calls=MAX_STALLED_CALLS,
            stall_ms=STALL_MS,
            query_failed_ms=rolling_failed_ms,
            write_failed_ms=write_failed_ms,
            problems=problems,
        )
        logger.info(
            "rollout took {s:.0f}s; {n} queries refused in the quiet window",
            s=roll_ended - roll_began,
            n=len(quiet_failed_ms),
        )
        for line in lines:
            (logger.error if "FAILED" in line else logger.info)(line)
        return 0 if passed else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
