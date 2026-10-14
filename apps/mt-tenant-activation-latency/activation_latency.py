"""
Does a rolling restart stall queries when a collection has many tenants?

Many HOT tenants, steady queries and writes across them, then roll the cluster and
compare the queries issued while it rolls against the quiet ones before. When a
restarted node catches up it reconciles tenant status, which walks every local HOT
shard inside the serial apply and delays every entry behind it. Writes to tenants
activate them, which lands on the same path. A third worker churns tenants through
the window -- create, write, read back, delete -- because those go through that
apply too, and the only lifecycle call used to be the seed, before the roll.

The restart QPS benchmark cannot see this: it drives one tenant per collection, so
neither the reconcile nor activation has anything to walk.

Three things beyond latency are measured, each because a build that passed every leg
here still regressed in a production-like environment: the window stays open
SETTLE_S past the rollout (the p99 spike peaked ~4min after it finished), the 4xx
the server itself counted, and the first FRESH_POD_WINDOW_S after each pod reports
ready.

Requires lazy shard loading (LAZY_LOAD_SHARD_COUNT_THRESHOLD=0); with it off shards
load eagerly on every build and there is nothing to tell apart.
"""

import itertools
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

from gates import (
    DeadlineAccumulator,
    Phase,
    RestartWatcher,
    UserErrorAccumulator,
    evaluate,
    evaluate_churn,
    evaluate_fresh_pod,
    evaluate_user_errors,
    get_env_float,
    get_env_int,
    within_any,
)

CLASS = os.getenv("CLASS_NAME", "MtRolloutLatency")
NAMESPACE = os.getenv("K8S_NAMESPACE", "weaviate")
LOCAL_CONTEXT_PREFIXES = ("kind-", "minikube", "docker-desktop", "k3d-")


def resolve_kube_context() -> str:
    """The context every kubectl call is pinned to, refusing a cluster this must not restart."""
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
            f"Refusing to run against kubectl context {ctx!r}: this test restarts the "
            f"weaviate StatefulSet. Expected a local cluster "
            f"({', '.join(LOCAL_CONTEXT_PREFIXES)}...), or set ALLOW_NON_LOCAL_CLUSTER=true."
        )
    return ctx


KUBE_CONTEXT = resolve_kube_context()


def kubectl(*args: str) -> List[str]:
    """kubectl argv with the context pinned, so it cannot drift mid-run."""
    return ["kubectl", "--context", KUBE_CONTEXT, *args]
TENANTS = get_env_int("TENANTS", 400)
SEED_WORKERS = get_env_int("SEED_WORKERS", 16)
QUERY_INTERVAL_S = get_env_float("QUERY_INTERVAL_S", 0.2)
WRITE_INTERVAL_S = get_env_float("WRITE_INTERVAL_S", 0.1)
BASELINE_S = get_env_float("BASELINE_S", 20)
# How long the window stays open after the rollout reports complete. 15s closed before
# the spike it exists to measure: the p99 peak landed ~4min after the roll finished.
SETTLE_S = get_env_float("SETTLE_S", 180)
# Percentiles, not extremes: the roll costs the client its port-forward, and one
# reconnect costs seconds even on a build that is fine.
MAX_QUERY_P99_MS = get_env_float("MAX_QUERY_P99_MS", 1000)
MAX_WRITE_P99_MS = get_env_float("MAX_WRITE_P99_MS", 500)
# A fixed ceiling allows a 7-20x degradation against the 43-139ms measured at rest,
# more than the regression that escaped, so bound the rolling p99 to this multiple
# of the leg's own quiet p99 as well.
MAX_QUERY_P99_RATIO = get_env_float("MAX_QUERY_P99_RATIO", 3.0)
# Below this the ratio is noise. 139ms is the top of the at-rest band, so a leg that
# stays inside what the suite measures at rest never fails on the ratio.
QUERY_P99_FLOOR_MS = get_env_float("QUERY_P99_FLOOR_MS", 139)
# Client-visible 4xx during the roll, which nothing used to count: 107 on the candidate
# against 39 on the baseline. A count, not a rate -- revisit if SETTLE_S or
# WRITE_INTERVAL_S move the window it accrues over.
MAX_USER_ERRORS = get_env_int("MAX_USER_ERRORS", 9)
MIN_QUERIES = get_env_int("MIN_QUERIES", 30)
# Only catches a write worker that never got going, whose p99 would be whatever its
# handful of samples happened to be.
MIN_WRITES = get_env_int("MIN_WRITES", 30)
# One create-write-read-delete round per interval. Unhurried on purpose: the point is
# to have the lifecycle running when the roll lands, not to load-test it.
CHURN_INTERVAL_S = get_env_float("CHURN_INTERVAL_S", 0.5)
# Measured quiet at create p99 95ms and delete 490ms, against 3,560 and 3,320ms under a
# rollout; these sit between. A p90 because a churn window holds tens of samples, where
# the nearest-rank p99 is just the slowest one.
MAX_TENANT_CREATE_P90_MS = get_env_float("MAX_TENANT_CREATE_P90_MS", 1000)
MAX_TENANT_DELETE_P90_MS = get_env_float("MAX_TENANT_DELETE_P90_MS", 1500)
# Enough samples that the slowest tenth being discarded still leaves a percentile
# worth reading, and a floor that catches a churn worker which barely ran.
MIN_TENANT_CHURNS = get_env_int("MIN_TENANT_CHURNS", 20)
# Separate from MAX_FAILURE_RATIO: a failed create or delete is the thing being
# measured, not a port-forward casualty, and the 50% that allows would hide it.
MAX_CHURN_FAILURES = get_env_int("MAX_CHURN_FAILURES", 3)
# Must not collide with the seeded tenant-N names: deleting a tenant the query and
# write workers drive would turn their gates into a measurement of this worker.
CHURN_PREFIX = os.getenv("CHURN_TENANT_PREFIX", "churn-")
ROLLOUT_TIMEOUT_S = get_env_int("ROLLOUT_TIMEOUT_S", 900)
# Deliberately wide: the roll costs the client its port-forward, so a few refusals are
# expected. This only catches a workload that spent the window erroring.
MAX_FAILURE_RATIO = get_env_float("MAX_FAILURE_RATIO", 0.5)
# A failed call that waited this long timed out rather than being refused: above the
# worst reconnect seen on a healthy build (15s), below the client's query timeouts.
STALL_MS = get_env_float("STALL_MS", 20_000)
# A couple, so one pathological reconnect cannot fail a run on its own, while the
# handful of timeouts a real stall produces does.
MAX_STALLED_CALLS = get_env_int("MAX_STALLED_CALLS", 2)
# These counters reset with the process, so the window has to be sampled while it
# is open rather than differenced across the roll. See gates.DeadlineAccumulator.
SCRAPE_INTERVAL_S = get_env_float("SCRAPE_INTERVAL_S", 5)
# Window after a pod reports ready, while its shards are still unopened stubs. Reported
# but not gated on latency: one port-forward cannot say which pod answered.
FRESH_POD_WINDOW_S = get_env_float("FRESH_POD_WINDOW_S", 15)
# Three pods x the window at QUERY_INTERVAL_S is ~225 queries; this only catches a
# window that measured nothing, with room for the port-forward dropping part of it.
MIN_FRESH_POD_QUERIES = get_env_int("MIN_FRESH_POD_QUERIES", 30)
# Longer than the worst single request on a healthy build (a ~15s port-forward
# reconnect), so a worker still blocked after this is a stall.
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
        kubectl(
            "get",
            "pods",
            "-n",
            NAMESPACE,
            "-o",
            'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{end}',
        ),
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
            kubectl(
                "get",
                "--raw",
                f"/api/v1/namespaces/{NAMESPACE}/pods/{pod}:2112/proxy/metrics",
            ),
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
            kubectl(*args, "-n", NAMESPACE), capture_output=True, text=True, check=False
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
        # Timed apart: they are ~5x apart at baseline, so one combined number would be gated at
        # the slower one's scale.
        creates: List[Sample] = []
        deletes: List[Sample] = []
        churns: List[Sample] = []
        # Churn tenants whose delete did not take. They would still grow the reconcile this
        # test measures, so they are swept once the load stops.
        leaked: List[str] = []
        stop = threading.Event()
        # Anything that went wrong collecting the measurement rather than in it. Appended to
        # from the worker threads; gates.evaluate fails the run on any of them.
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
                    # The wait is kept either way; only `ok` decides whether the cluster served it. An
                    # instant refusal must not fill min_queries, and a 30s timeout must not be dropped.
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

        churn_seq = itertools.count()

        def sweep(name: str) -> None:
            """Best effort removal. A create that timed out may still have committed."""
            try:
                col.tenants.remove([name])
            except Exception as e:
                leaked.append(name)
                logger.warning("could not sweep {t}: {e}", t=name, e=e)

        def churn_once(_: str) -> None:
            """Create a tenant, write to it, read it back, delete it.

            Takes the loop's tenant argument and ignores it. This worker needs a
            name nothing else holds: it deletes what it creates, and churning one
            of the seeded tenants would delete a tenant the query and write
            workers are driving, so their numbers would be measuring this worker.
            The names are unique per iteration as well as distinct from the
            seeded set, so a tenant a failed delete left behind cannot collide
            with a later iteration and turn one bad delete into a run of 422s.

            Raises on anything that did not complete, which the loop records as an
            unserved iteration and logs. Tolerant by design, like the other
            measurement loops and unlike the seed: one transient error must not
            void the leg.
            """
            name = f"{CHURN_PREFIX}{next(churn_seq)}"

            began = time.monotonic()
            try:
                col.tenants.create(Tenant(name=name))
            except Exception:
                creates.append((began, (time.monotonic() - began) * 1000, False))
                sweep(name)
                raise
            creates.append((began, (time.monotonic() - began) * 1000, True))

            write_error: Optional[Exception] = None
            try:
                written = col.with_tenant(name).data.insert({"body": "churn"}, vector=VEC)
                if col.with_tenant(name).query.fetch_object_by_id(written) is None:
                    # Not an error: the write is acked at consistency ONE, so the read
                    # can legitimately land on a replica that has not applied it yet.
                    logger.warning("churn read-back on {t} found nothing", t=name)
            except Exception as e:
                write_error = e

            # Attempted whether or not the write did, so a failed write cannot leave the tenant
            # behind and have the worker measure its own leftovers.
            began = time.monotonic()
            try:
                col.tenants.remove([name])
            except Exception:
                deletes.append((began, (time.monotonic() - began) * 1000, False))
                leaked.append(name)
                raise
            deletes.append((began, (time.monotonic() - began) * 1000, True))
            if write_error is not None:
                raise write_error

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
            threading.Thread(
                target=worker,
                name="churn",
                daemon=True,
                args=(churns, CHURN_INTERVAL_S, churn_once, "churn"),
            ),
        ]
        for t in threads:
            t.start()

        # Baselined before the quiet load so one accumulator covers both windows: the scrape
        # closing the quiet window is the one opening the rolling one.
        user_errors: Optional[UserErrorAccumulator] = None
        try:
            user_errors = UserErrorAccumulator(scrape_every_pod())
        except Exception as e:
            problems.append(f"could not baseline the 4xx counters on every pod: {e}")
            logger.error("could not baseline the 4xx counters: {e}", e=e)

        logger.info("{s:.0f}s of quiet load for a baseline", s=BASELINE_S)
        time.sleep(BASELINE_S)
        quiet_end = time.monotonic()

        accumulator: Optional[DeadlineAccumulator] = None
        watcher: Optional[RestartWatcher] = None
        quiet_user_errors: Optional[int] = None
        try:
            baseline_texts = scrape_every_pod()
            accumulator = DeadlineAccumulator(baseline_texts)
            # Every pod's process identity as it was before the roll, so the
            # first restart of each is the one that gets timed.
            watcher = RestartWatcher(baseline_texts)
            if user_errors is not None:
                user_errors.observe(baseline_texts)
                quiet_user_errors = user_errors.total()
        except Exception as e:
            problems.append(f"could not baseline the deadline counters on every pod: {e}")
            logger.error("could not baseline the deadline counters: {e}", e=e)

        scrape_stop = threading.Event()

        def deadline_sampler() -> None:
            try:
                while not scrape_stop.is_set():
                    try:
                        # Mid-roll a pod, or the API server, is legitimately unreachable: take whoever answers,
                        # since the accumulator resumes a pod on its next sample and the final complete scrape
                        # catches a kubectl that stayed broken. One scrape serves all three readings, and is
                        # timestamped before it so a slow one cannot shift the fresh-pod window.
                        at = time.monotonic()
                        texts = scrape_pods()
                        accumulator.observe(texts)
                        if user_errors is not None:
                            user_errors.observe(texts)
                        if watcher is not None:
                            watcher.observe(texts, at)
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
                # A worker still inside a call holds the slowest sample of the run; evaluating without
                # it would pass on the fast ones that came before.
                problems.append(
                    f"the {t.name} worker was still waiting on a request"
                    f" {WORKER_DRAIN_S:.0f}s after being told to stop, so its"
                    " slowest sample never reached the gate"
                )

        # Snapshotted once: the churn worker may still be alive. The sweep runs after the final
        # scrape, since removing an already-removed tenant can answer 422.
        leaked_names = sorted(set(leaked))

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
        roll_user_errors: Optional[int] = None
        fresh_windows: Optional[int] = None
        fresh_slices: List[Tuple[float, float]] = []
        if accumulator is not None:
            try:
                # The final scrape has to be complete, unlike the sampler's.
                at = time.monotonic()
                final_texts = scrape_every_pod()
                accumulator.observe(final_texts)
                deadline_waits = accumulator.total()
                if user_errors is not None:
                    user_errors.observe(final_texts)
                    total = user_errors.total()
                    if total is not None:
                        # A missing quiet count is treated as zero, charging its 4xx to the roll. That
                        # overstates rather than understates.
                        roll_user_errors = max(0, total - (quiet_user_errors or 0))
                if watcher is not None:
                    watcher.observe(final_texts, at)
                    fresh_slices = watcher.windows(FRESH_POD_WINDOW_S)
                    fresh_windows = len(fresh_slices)
            except Exception as e:
                problems.append(f"could not complete the final deadline scrape: {e}")
                logger.error("could not complete the final deadline scrape: {e}", e=e)

        if leaked_names:
            # Swept so they stop weighing on the cluster, and reported because a delete that does
            # not take is the regression this worker is here for.
            logger.warning(
                "{n} churn tenants outlived their iteration: {t}",
                n=len(leaked_names),
                t=leaked_names[:10],
            )
            try:
                col.tenants.remove(leaked_names)
            except Exception as e:
                logger.warning("could not sweep the leaked churn tenants: {e}", e=e)

        create_quiet_ms, create_quiet_failed_ms = within(creates, 0, quiet_end)
        create_roll_ms, create_roll_failed_ms = within(creates, roll_began, roll_window_end)
        delete_quiet_ms, delete_quiet_failed_ms = within(deletes, 0, quiet_end)
        delete_roll_ms, delete_roll_failed_ms = within(deletes, roll_began, roll_window_end)
        churn_ms, churn_failed_ms = within(churns, roll_began, roll_window_end)

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
            max_query_p99_ratio=MAX_QUERY_P99_RATIO,
            query_p99_floor_ms=QUERY_P99_FLOOR_MS,
            max_failure_ratio=MAX_FAILURE_RATIO,
            max_stalled_calls=MAX_STALLED_CALLS,
            stall_ms=STALL_MS,
            query_failed_ms=rolling_failed_ms,
            write_failed_ms=write_failed_ms,
            problems=problems,
        )
        churn_passed, churn_lines = evaluate_churn(
            Phase(create_quiet_ms, create_roll_ms, create_roll_failed_ms),
            Phase(delete_quiet_ms, delete_roll_ms, delete_roll_failed_ms),
            MAX_TENANT_CREATE_P90_MS,
            MAX_TENANT_DELETE_P90_MS,
            MIN_TENANT_CHURNS,
            failures=len(churn_failed_ms),
            attempts=len(churn_ms) + len(churn_failed_ms),
            max_failures=MAX_CHURN_FAILURES,
        )
        # Carved out of the same query samples the rolling gate reads. Overlapping windows are
        # fine; within_any takes a sample once.
        fresh_ms, fresh_failed_ms = within_any(queries, fresh_slices)
        fresh_passed, fresh_lines = evaluate_fresh_pod(
            Phase(quiet_ms, fresh_ms, fresh_failed_ms),
            fresh_windows,
            FRESH_POD_WINDOW_S,
            MIN_FRESH_POD_QUERIES,
        )
        errors_passed, error_lines = evaluate_user_errors(
            quiet_user_errors,
            roll_user_errors,
            MAX_USER_ERRORS,
        )
        passed = passed and churn_passed and fresh_passed and errors_passed
        lines = list(lines) + churn_lines + fresh_lines + error_lines
        logger.info(
            "rollout took {s:.0f}s; {n} queries refused in the quiet window",
            s=roll_ended - roll_began,
            n=len(quiet_failed_ms),
        )
        # Not gated: it is the reference, not the measurement. Logged because a tenant create
        # failing on an idle cluster is worth a look.
        logger.info(
            "quiet window: {c} tenant creates and {d} deletes failed; {n} tenants leaked",
            c=len(create_quiet_failed_ms),
            d=len(delete_quiet_failed_ms),
            n=len(leaked_names),
        )
        for line in lines:
            (logger.error if "FAILED" in line else logger.info)(line)
        return 0 if passed else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
