"""Typed MOVE worker. Two paths — legitimate moves and deliberate conflicts (Guard H).

Legitimate: <=1 MOVE per shard, source in current replicas, target = the non-holder, polled to
terminal. Deliberate conflict: a second MOVE fired on a busy shard from the SAME source+target as
the inflight op, EXPECTING the busy-replica rejection (HTTP 500 + 'replica is already being
replicated'); an unexpected success is the exact invariant breach we guard against (FINDING).

Per-op poller tasks are tracked in a set and reaped on shutdown so none are orphaned.
"""

import asyncio
import random

import weaviate
from loguru import logger

import topology
from clients import Clients
from config import Config
from model import Findings, Model, exc_status_code, has_substring

_BUSY_SUBSTRING = "replica is already being replicated"


async def moves_worker(
    stop: asyncio.Event,
    clients: Clients,
    cfg: Config,
    model: Model,
    findings: Findings,
) -> None:
    coord = clients.coordinator
    all_nodes = [spec.name for spec in cfg.nodes]
    pollers: set[asyncio.Task] = set()
    try:
        while not stop.is_set():
            busy = [(s, op) for s, op in model.inflight.items() if op != "PENDING"]
            if busy and random.random() < cfg.move_conflict_inject_rate:
                await _inject_conflict(coord, cfg, model, findings, busy)
            else:
                await _legit_move(coord, cfg, model, findings, pollers, all_nodes)
            if cfg.move_interval_ms:
                await asyncio.sleep(cfg.move_interval_ms / 1000.0)
    finally:
        for p in pollers:
            p.cancel()
        await asyncio.gather(*pollers, return_exceptions=True)


async def _legit_move(
    coord: weaviate.WeaviateAsyncClient,
    cfg: Config,
    model: Model,
    findings: Findings,
    pollers: set[asyncio.Task],
    all_nodes: list[str],
) -> None:
    try:
        state = await topology.sharding_state(coord, cfg.collection)
    except Exception as e:
        logger.warning("Could not read sharding state; skipping move this iter: {e!r}", e=e)
        return

    picked = _pick_movable(cfg, model, state, all_nodes)
    if picked is None:
        return
    shard, replicas, target = picked

    # Await-free reservation: re-check under the lock so two iterations never double-book a shard
    # or exceed the inflight cap.
    async with model.moves_lock:
        if shard in model.inflight or len(model.inflight) >= cfg.move_max_inflight:
            return
        model.inflight[shard] = "PENDING"

    source = random.choice(replicas)
    try:
        op_id = await topology.replicate_move(coord, cfg.collection, shard, source, target)
    except Exception as e:
        async with model.moves_lock:
            if model.inflight.get(shard) == "PENDING":
                model.inflight.pop(shard, None)
        _categorize_start_error(e, model, findings, shard)
        return

    async with model.moves_lock:
        model.inflight[shard] = op_id
    model.counters.moves_started += 1
    logger.info(
        "MOVE started shard={shard} {src}->{tgt} op={op}",
        shard=shard,
        src=source,
        tgt=target,
        op=op_id,
    )
    poller = asyncio.create_task(_poll_to_terminal(coord, cfg, model, findings, shard, op_id))
    pollers.add(poller)
    poller.add_done_callback(pollers.discard)


def _pick_movable(
    cfg: Config,
    model: Model,
    state: dict[str, list[str]],
    all_nodes: list[str],
) -> tuple[str, list[str], str] | None:
    candidates = [
        t
        for t in cfg.tenant_names
        if model.tenant_status.get(t) == "ACTIVE" and t not in model.inflight and t in state
    ]
    random.shuffle(candidates)
    for shard in candidates:
        replicas = state.get(shard, [])
        if not replicas:
            continue
        target = topology.target_for(replicas, all_nodes)
        if target is None:
            continue
        return shard, replicas, target
    return None


async def _poll_to_terminal(
    coord: weaviate.WeaviateAsyncClient,
    cfg: Config,
    model: Model,
    findings: Findings,
    shard: str,
    op_id: str,
) -> None:
    while True:
        await asyncio.sleep(cfg.move_poll_interval)
        try:
            op = await topology.get_replication_op(coord, op_id)
        except Exception as e:
            logger.warning(
                "poll op={op} shard={shard} transient error: {e!r}", op=op_id, shard=shard, e=e
            )
            continue
        errors = topology.op_errors(op)
        if errors:
            findings.add(f"move op={op_id} shard={shard} reported errors: {errors}")
        state = topology.op_state(op)
        if topology.is_terminal(state):
            if state == "READY":
                model.counters.moves_completed += 1
            logger.info(
                "MOVE op={op} shard={shard} reached terminal state {st}",
                op=op_id,
                shard=shard,
                st=state,
            )
            async with model.moves_lock:
                if model.inflight.get(shard) == op_id:
                    model.inflight.pop(shard, None)
            return


async def _inject_conflict(
    coord: weaviate.WeaviateAsyncClient,
    cfg: Config,
    model: Model,
    findings: Findings,
    busy: list[tuple[str, str]],
) -> None:
    shard, op_id = random.choice(busy)
    try:
        op = await topology.get_replication_op(coord, op_id)
    except Exception as e:
        logger.warning("conflict inject: could not read inflight op {op}: {e!r}", op=op_id, e=e)
        return
    if topology.is_terminal(topology.op_state(op)):
        return  # the op finished before we could conflict with it
    source = topology.op_source(op)
    target = topology.op_target(op)
    if not source or not target:
        logger.warning("conflict inject: op {op} missing source/target; skipping", op=op_id)
        return

    logger.info(
        "Injecting conflicting MOVE on busy shard={shard} {src}->{tgt}",
        shard=shard,
        src=source,
        tgt=target,
    )
    try:
        dup = await topology.replicate_move(coord, cfg.collection, shard, source, target)
    except Exception as e:
        _classify_conflict(e, model, findings, shard)
        return

    # A second MOVE on a shard already being replicated from the same source must be rejected.
    findings.add(
        f"Guard H breach: second MOVE on busy shard {shard} was ADMITTED as op {dup} "
        f"(two concurrent moves on one replica)"
    )
    try:
        await topology.cancel_replication(coord, dup)
    except Exception as e:
        logger.warning("could not cancel wrongly-admitted dup move {op}: {e!r}", op=dup, e=e)


def _classify_conflict(e: Exception, model: Model, findings: Findings, shard: str) -> None:
    code = exc_status_code(e)
    if has_substring(e, _BUSY_SUBSTRING):
        model.counters.move_conflict_rejected += 1
        if code != 500:
            logger.warning(
                "Guard H fired on {shard} but status was {code} (expected 500): {e!r}",
                shard=shard,
                code=code,
                e=e,
            )
        else:
            logger.info(
                "Guard H fired: conflicting MOVE on {shard} rejected (HTTP 500)", shard=shard
            )
    elif code == 422:
        model.counters.move_422 += 1
        logger.info("conflicting MOVE on {shard} got 422 validation: {e!r}", shard=shard, e=e)
    else:
        findings.add(
            f"conflicting MOVE on {shard} got unexpected error "
            f"(expected 500+'{_BUSY_SUBSTRING}'): code={code} {e!r}"
        )


def _categorize_start_error(e: Exception, model: Model, findings: Findings, shard: str) -> None:
    code = exc_status_code(e)
    if code == 422:
        # Sharding state changed between our snapshot and the call (e.g. target now holds the
        # shard); a validation 422 is an expected, counted blunder, not a breach.
        model.counters.move_422 += 1
        logger.info("legit MOVE on {shard} rejected 422 (state changed): {e!r}", shard=shard, e=e)
    elif has_substring(e, _BUSY_SUBSTRING):
        logger.warning(
            "legit MOVE on {shard} unexpectedly hit busy-replica; will retry: {e!r}",
            shard=shard,
            e=e,
        )
    else:
        findings.add(f"legit MOVE on {shard} unexpected error: code={code} {e!r}")
