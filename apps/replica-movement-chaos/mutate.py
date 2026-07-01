"""CL.ALL insert/update/delete, distributed across node clients.

Correctness rule: commit to the model ONLY after a CL.ALL ack. Every attempt re-selects a
random per-pod client (LB emulation); because retries re-randomize, a coordinator briefly
unavailable mid-move self-heals on the next attempt. Deterministic ids make retries idempotent.
"""

import asyncio
import random
import uuid
from typing import Any

from loguru import logger
from weaviate.classes.config import ConsistencyLevel

from clients import Clients
from config import Config
from model import (
    AckBudgetExceeded,
    Findings,
    Model,
    Outcome,
    exc_status_code,
    has_substring,
    is_transient_error,
    retry_until_ack,
)
from setup import object_id


async def mutate_supervisor(
    stop: asyncio.Event,
    clients: Clients,
    cfg: Config,
    model: Model,
    findings: Findings,
) -> None:
    workers = [
        asyncio.create_task(_mutate_worker(i, stop, clients, cfg, model, findings))
        for i in range(cfg.mutate_concurrency)
    ]
    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise


async def _mutate_worker(
    wid: int,
    stop: asyncio.Event,
    clients: Clients,
    cfg: Config,
    model: Model,
    findings: Findings,
) -> None:
    while not stop.is_set():
        t = random.choice(cfg.tenant_names)
        try:
            async with model.locks[t]:
                await _do_one_mutation(t, clients, cfg, model)
        except AckBudgetExceeded as e:
            findings.add(f"mutate worker {wid}: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            findings.add(f"mutate worker {wid} permanent error on tenant {t}: {e!r}")
        if cfg.mutate_interval_ms:
            await asyncio.sleep(cfg.mutate_interval_ms / 1000.0)


def _make_ct(client: Any, cfg: Config, tenant: str) -> Any:
    return (
        client.collections.get(cfg.collection)
        .with_tenant(tenant)
        .with_consistency_level(ConsistencyLevel.ALL)
    )


async def _do_one_mutation(t: str, clients: Clients, cfg: Config, model: Model) -> None:
    r = random.random()
    existing = model.random_existing_id(t)

    if existing is None or r < 0.4:
        await _insert(t, clients, cfg, model)
    elif r < 0.8:
        await _update(t, existing, clients, cfg, model)
    else:
        await _delete(t, existing, clients, cfg, model)


async def _insert(t: str, clients: Clients, cfg: Config, model: Model) -> None:
    idx = model.reserve_idx(t)
    oid = object_id(t, idx)
    payload = {"payload": f"ins-{t}-{idx}-{uuid.uuid4().hex[:8]}", "seq": idx}

    async def op_fn() -> Any:
        ct = _make_ct(clients.random_node_client(), cfg, t)
        return await ct.data.insert(properties=payload, uuid=oid)

    def classify(_result: Any, exc: BaseException | None) -> Outcome:
        if exc is None:
            return Outcome.ACK
        if exc_status_code(exc) == 422 and has_substring(exc, "already exists"):
            return Outcome.ACK  # a prior attempt already committed this idempotent write
        if is_transient_error(exc):
            return Outcome.RETRY
        raise exc

    await retry_until_ack(
        op_fn,
        classify,
        budget=cfg.retry_budget,
        backoff_ms=cfg.retry_backoff_ms,
        label=f"insert {t}/{oid}",
    )
    model.objects[t][oid] = payload
    model.counters.inserts += 1


async def _update(t: str, oid: str, clients: Clients, cfg: Config, model: Model) -> None:
    seq = random.randint(0, 1_000_000)
    payload = {"payload": f"upd-{t}-{uuid.uuid4().hex[:8]}", "seq": seq}

    async def op_fn() -> Any:
        ct = _make_ct(clients.random_node_client(), cfg, t)
        return await ct.data.replace(uuid=oid, properties=payload)

    def classify(_result: Any, exc: BaseException | None) -> Outcome:
        if exc is None:
            return Outcome.ACK
        # A replica briefly missing the object mid-move surfaces 404; the idempotent CL.ALL
        # replace heals once the move settles, so retry rather than fail.
        if exc_status_code(exc) == 404 or has_substring(exc, "not found"):
            return Outcome.RETRY
        if is_transient_error(exc):
            return Outcome.RETRY
        raise exc

    await retry_until_ack(
        op_fn,
        classify,
        budget=cfg.retry_budget,
        backoff_ms=cfg.retry_backoff_ms,
        label=f"update {t}/{oid}",
    )
    model.objects[t][oid] = payload
    model.counters.updates += 1


async def _delete(t: str, oid: str, clients: Clients, cfg: Config, model: Model) -> None:
    async def op_fn() -> Any:
        ct = _make_ct(clients.random_node_client(), cfg, t)
        return await ct.data.delete_by_id(oid)

    def classify(result: Any, exc: BaseException | None) -> Outcome:
        if exc is None:
            return Outcome.ACK  # True (deleted) and False (already gone) are both an ack
        if exc_status_code(exc) == 404 or has_substring(exc, "not found"):
            return Outcome.ACK
        if is_transient_error(exc):
            return Outcome.RETRY
        raise exc

    await retry_until_ack(
        op_fn,
        classify,
        budget=cfg.retry_budget,
        backoff_ms=cfg.retry_backoff_ms,
        label=f"delete {t}/{oid}",
    )
    model.objects[t].pop(oid, None)
    model.counters.deletes += 1
    logger.trace("deleted {t}/{oid}", t=t, oid=oid)
