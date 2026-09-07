"""A single backup overlapped with moves. Isolated doc-sourced ``client.backup.*``.

The backup deliberately races the moves (the move x backup corruption concern). It gets its own
bounded budget so the drain-time gather backstop never cancels an otherwise-fine backup into a
spurious FAILED — a timeout/cancel is recorded as a partial status.
"""

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

import weaviate
from loguru import logger
from weaviate.classes.backup import BackupStorage

from clients import Clients
from config import Config
from model import Model


@dataclass
class BackupState:
    enabled: bool
    backup_id: str | None = None
    status: str | None = None  # SUCCESS / FAILED / TIMEOUT / CANCELLED_DRAIN / None
    completed: bool = False
    error: str | None = None
    # Per-tenant object count captured (from the live model) just before backup.create. Approximate
    # by design — the restore check uses it only as a LOWER bound (C8/F8).
    backup_snapshot: dict[str, int] = field(default_factory=dict)


def _status_of(res: Any) -> str:
    status = getattr(res, "status", res)
    name = getattr(status, "name", None)
    return str(name).upper() if name is not None else str(status).upper()


async def backup_worker(
    stop: asyncio.Event,
    clients: Clients,
    cfg: Config,
    model: Model,
    state: BackupState,
) -> None:
    if not cfg.backup_enabled:
        logger.info("Backup disabled (BACKUP_ENABLED=false); skipping backup workflow")
        return

    delay = random.randint(cfg.backup_delay_min, cfg.backup_delay_max)
    logger.info("Backup will start in {d}s (overlapping active moves)", d=delay)
    # Sleep up to `delay`, but wake early if the run is already stopping so a short DURATION
    # still gets its one-shot backup rather than skipping it.
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except asyncio.TimeoutError:
        pass

    bid = f"{cfg.collection.lower()}-{uuid.uuid4().hex[:12]}"
    backend = BackupStorage[cfg.backup_backend.upper()]
    state.backup_id = bid
    # Snapshot per-tenant counts at backup START as the restore lower bound. Synchronous dict-comp
    # (no await) so it reads a coherent instant of the live model without racing the mutate workers.
    state.backup_snapshot = {t: len(model.objects[t]) for t in cfg.tenant_names}
    logger.info("Starting backup {bid} (backend={be})", bid=bid, be=cfg.backup_backend)

    try:
        res = await asyncio.wait_for(
            clients.coordinator.backup.create(
                backup_id=bid,
                backend=backend,
                include_collections=[cfg.collection],
                wait_for_completion=True,
            ),
            timeout=cfg.drain_timeout,
        )
        state.status = _status_of(res)
        state.completed = True
        logger.info("Backup {bid} finished with status {s}", bid=bid, s=state.status)
    except asyncio.TimeoutError:
        state.status = "TIMEOUT"
        state.error = f"backup did not complete within {cfg.drain_timeout}s"
        logger.error("Backup {bid} timed out after {t}s", bid=bid, t=cfg.drain_timeout)
    except asyncio.CancelledError:
        if not state.completed:
            state.status = state.status or "CANCELLED_DRAIN"
            state.error = "backup cancelled by drain backstop"
        raise
    except Exception as e:
        state.status = "FAILED"
        state.error = repr(e)
        logger.error("Backup {bid} raised: {e!r}", bid=bid, e=e)


async def restore_backup(coord: weaviate.WeaviateAsyncClient, cfg: Config, backup_id: str) -> str:
    """Restore a completed backup, recreating the ORIGINAL collection + its tenants. Isolated
    doc-sourced ``client.backup.restore``, mirroring the create path: ``wait_for_completion`` drives
    the restore-status poll internally and raises on a FAILED/CANCELED terminal. Returns the terminal
    status string ('SUCCESS' / 'FAILED' / 'TIMEOUT'). Live-validated only (D4)."""
    backend = BackupStorage[cfg.backup_backend.upper()]
    logger.info("Restoring backup {bid} (backend={be})", bid=backup_id, be=cfg.backup_backend)
    try:
        res = await asyncio.wait_for(
            coord.backup.restore(
                backup_id=backup_id,
                backend=backend,
                include_collections=[cfg.collection],
                wait_for_completion=True,
            ),
            timeout=cfg.restore_timeout,
        )
        return _status_of(res)
    except asyncio.TimeoutError:
        logger.error("Restore of {bid} timed out after {t}s", bid=backup_id, t=cfg.restore_timeout)
        return "TIMEOUT"
    except Exception as e:  # BackupFailedException / connection error -> a FINDING at the caller
        logger.error("Restore of {bid} raised: {e!r}", bid=backup_id, e=e)
        return "FAILED"
