"""Replica-movement-under-chaos e2e entrypoint.

Orchestration: build node-pinned clients -> pre-flight -> create+seed at CL.ALL -> run 4 concurrent
chaos workers for DURATION -> cooperative stop -> drain/settle/verify -> exit 0 (pass) / 1 (finding).
Invoked as `python3 run.py` (single mode, no subcommand).
"""

import asyncio
import random
import sys

import weaviate
from loguru import logger

import setup as setup_mod
import topology
from backup import BackupState, backup_worker
from clients import Clients
from config import Config
from model import Findings, Model, exc_status_code, has_substring
from moves import moves_worker
from mutate import mutate_supervisor
from tenants import tenants_worker
from verify import verify


class PreflightError(Exception):
    """Cluster is not in a state the test can run against (fail-fast, no silent no-op)."""


async def main() -> int:
    cfg = Config.from_env()
    if cfg.seed:
        random.seed(cfg.seed)
    _log_config(cfg)

    clients = Clients(cfg)
    findings = Findings()
    backup_state = BackupState(enabled=cfg.backup_enabled)

    try:
        await clients.connect()
        _cross_check_nodes(clients, cfg)

        coord = clients.coordinator
        await setup_mod.create_collection(coord, cfg)
        await _probe_replica_movement(coord, cfg)
        await setup_mod.create_tenants(coord, cfg)

        model = Model(cfg.tenant_names)
        await setup_mod.seed(coord, cfg, model)

        await _preflight_move(coord, cfg)

        stop = asyncio.Event()
        tasks = [
            asyncio.create_task(tenants_worker(stop, clients, cfg, model, findings)),
            asyncio.create_task(mutate_supervisor(stop, clients, cfg, model, findings)),
            asyncio.create_task(backup_worker(stop, clients, cfg, model, backup_state)),
            asyncio.create_task(moves_worker(stop, clients, cfg, model, findings)),
        ]
        logger.info("Chaos running for {d}s ...", d=cfg.duration)
        await asyncio.sleep(cfg.duration)
        stop.set()
        logger.info("Duration elapsed; stopping workers (drain budget {t}s)", t=cfg.drain_timeout)

        results = await _drain_tasks(tasks, cfg.drain_timeout)
        _inspect_results(results, findings)

        await verify(clients, cfg, model, backup_state, findings)
        _assert_chaos_landed(model, findings)
        logger.info("Counters: {c}", c=model.counters.summary())
    except PreflightError as e:
        logger.error("Pre-flight failed: {e}", e=e)
        findings.add(f"pre-flight: {e}")
    except Exception as e:
        logger.exception("Unhandled error during run")
        findings.add(f"unhandled error: {e!r}")
    finally:
        await clients.close()

    if findings.ok:
        logger.success("PASS: no findings")
        return 0
    logger.error("FAIL: {n} finding(s)", n=len(findings.messages))
    for m in findings.messages:
        logger.error("  - {m}", m=m)
    return 1


def _log_config(cfg: Config) -> None:
    logger.info(
        "Config: collection={c} tenants={t} objects/tenant={o} rf={rf} duration={d}s "
        "nodes={nodes} backup={be}/{backend} move_max_inflight={mmi} "
        "conflict_rates(tenant={tcr}, move={mcr})",
        c=cfg.collection,
        t=cfg.tenant_count,
        o=cfg.objects_per_tenant,
        rf=cfg.rf,
        d=cfg.duration,
        nodes=[n.name for n in cfg.nodes],
        be=cfg.backup_enabled,
        backend=cfg.backup_backend,
        mmi=cfg.move_max_inflight,
        tcr=cfg.tenant_conflict_inject_rate,
        mcr=cfg.move_conflict_inject_rate,
    )


def _cross_check_nodes(clients: Clients, cfg: Config) -> None:
    http_base = f"http://{cfg.nodes[0].http_host}:{cfg.nodes[0].http_port}"
    try:
        server_names = set(topology.node_names(http_base))
    except Exception as e:
        raise PreflightError(f"could not list nodes from {http_base}/v1/nodes: {e!r}") from e
    configured = {n.name for n in cfg.nodes}
    missing = configured - server_names
    if missing:
        raise PreflightError(
            f"WEAVIATE_NODES names {sorted(missing)} not present in server nodes {sorted(server_names)}; "
            "MOVE requires exact node-name matches"
        )
    logger.info("Node-name cross-check OK: {names}", names=sorted(configured))


async def _probe_replica_movement(coord: weaviate.WeaviateAsyncClient, cfg: Config) -> None:
    try:
        state = await topology.sharding_state(coord, cfg.collection)
    except Exception as e:
        code = exc_status_code(e)
        if (
            code == 501
            or has_substring(e, "not implemented")
            or has_substring(e, "replica movement")
        ):
            raise PreflightError(
                "replica movement appears disabled (set REPLICA_MOVEMENT_ENABLED=true on all nodes): "
                f"{e!r}"
            ) from e
        raise PreflightError(f"sharding-state probe failed: {e!r}") from e
    logger.info("Replica movement enabled; sharding state reports {n} shard(s)", n=len(state))


async def _preflight_move(coord: weaviate.WeaviateAsyncClient, cfg: Config) -> None:
    """Prove the movement feature actually works before the chaos loop begins.

    A disabled/misconfigured/broken MOVE must fail fast HERE with a clear message rather than
    silently yielding a vacuous green (a run that never moved anything has proven nothing). This is
    stronger than the 501 sharding-state probe: it drives a real MOVE to READY. Not counted in
    model.counters, so the chaos-loop zero-move FINDING stays meaningful.
    """
    all_nodes = [spec.name for spec in cfg.nodes]
    try:
        state = await topology.sharding_state(coord, cfg.collection)
    except Exception as e:
        raise PreflightError(f"pre-flight MOVE: could not read sharding state: {e!r}") from e

    picked = _pick_preflight_shard(cfg, state, all_nodes)
    if picked is None:
        raise PreflightError(
            "pre-flight MOVE: no shard has a legal (source, target) pair (rf == node count?); "
            "the movement workflow cannot be exercised on this topology"
        )
    shard, source, target = picked
    logger.info(
        "Pre-flight MOVE {shard} {src}->{tgt} (must reach READY within {t}s)",
        shard=shard,
        src=source,
        tgt=target,
        t=cfg.preflight_move_timeout,
    )
    try:
        op_id = await topology.replicate_move(coord, cfg.collection, shard, source, target)
    except Exception as e:
        raise PreflightError(
            f"pre-flight MOVE on {shard} was rejected (movement disabled or broken?): {e!r}"
        ) from e

    final = await _await_move_ready(coord, cfg, op_id, shard)
    if final != "READY":
        raise PreflightError(
            f"pre-flight MOVE on {shard} did not reach READY (terminal={final}); "
            "the movement feature is not functioning"
        )
    logger.success("Pre-flight MOVE reached READY — movement feature confirmed working")


def _pick_preflight_shard(
    cfg: Config, state: dict[str, list[str]], all_nodes: list[str]
) -> tuple[str, str, str] | None:
    for shard in cfg.tenant_names:
        replicas = state.get(shard, [])
        if not replicas:
            continue
        target = topology.target_for(replicas, all_nodes)
        if target is None:
            continue
        return shard, replicas[0], target
    return None


async def _await_move_ready(
    coord: weaviate.WeaviateAsyncClient, cfg: Config, op_id: str, shard: str
) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + cfg.preflight_move_timeout
    while loop.time() < deadline:
        await asyncio.sleep(cfg.move_poll_interval)
        try:
            op = await topology.get_replication_op(coord, op_id)
        except Exception as e:
            logger.warning("pre-flight MOVE {shard} poll transient error: {e!r}", shard=shard, e=e)
            continue
        state = topology.op_state(op)
        if topology.is_terminal(state):
            return state
    return "TIMEOUT"


def _assert_chaos_landed(model: Model, findings: Findings) -> None:
    """Zero completed moves over the whole run is a hard FINDING (the feature was never exercised).
    Guards G/H never firing only warns — the conflict windows are probabilistic."""
    c = model.counters
    if c.moves_completed == 0:
        findings.add(
            "zero moves completed during the chaos run — replica movement was never exercised, "
            "so a green would prove nothing"
        )
    if c.move_conflict_rejected == 0:
        logger.warning(
            "Guard H never fired: no conflicting-MOVE rejection observed — the busy-replica "
            "conflict window may not have been hit this run"
        )
    if c.tenant_move_conflict_rejected == 0:
        logger.warning(
            "Guard G never fired: no deactivate-during-move rejection observed — the tenant "
            "conflict window may not have been hit this run"
        )


async def _drain_tasks(tasks: list[asyncio.Task], timeout: int) -> list:
    try:
        return await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.error("Workers did not stop within {t}s; cancelling", t=timeout)
        for t in tasks:
            t.cancel()
        return await asyncio.gather(*tasks, return_exceptions=True)


def _inspect_results(results: list, findings: Findings) -> None:
    for r in results:
        if isinstance(r, asyncio.CancelledError):
            continue  # expected when the drain backstop cancels a still-running worker
        if isinstance(r, BaseException):
            findings.add(f"worker raised unexpectedly: {r!r}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
