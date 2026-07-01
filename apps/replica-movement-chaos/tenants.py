"""Single-tenant activate/deactivate toggles, asserting the move guard (Guard G).

Single-tenant calls only: batch UpdateTenants has partial-update semantics, so one tenant per
call is required for a clean 422 assertion. DEACTIVATE mid-move is the guarded path (HTTP 422 +
'replica movement in progress'); ACTIVATE is NOT guarded and must always succeed.
"""

import asyncio
import random
from typing import Any

from loguru import logger

from clients import Clients
from config import Config
from model import Findings, Model, exc_status_code, has_substring

_MOVE_GUARD_SUBSTRING = "replica movement in progress"


async def tenants_worker(
    stop: asyncio.Event,
    clients: Clients,
    cfg: Config,
    model: Model,
    findings: Findings,
) -> None:
    col = clients.coordinator.collections.get(cfg.collection)
    while not stop.is_set():
        tenant, force_deactivate = _pick(cfg, model)
        if force_deactivate or model.tenant_status[tenant] == "ACTIVE":
            await _deactivate(col, tenant, model, findings)
        else:
            await _activate(col, tenant, model, findings)
        if cfg.tenant_interval_ms:
            await asyncio.sleep(cfg.tenant_interval_ms / 1000.0)


def _pick(cfg: Config, model: Model) -> tuple[str, bool]:
    """Bias toward deactivating a tenant with an inflight move to reliably exercise Guard G."""
    inflight_tenants = list(model.inflight.keys())
    if inflight_tenants and random.random() < cfg.tenant_conflict_inject_rate:
        return random.choice(inflight_tenants), True
    return random.choice(cfg.tenant_names), False


async def _deactivate(col: Any, tenant: str, model: Model, findings: Findings) -> None:
    try:
        await col.tenants.deactivate([tenant])
        model.tenant_status[tenant] = "INACTIVE"
        model.counters.deactivations += 1
    except Exception as e:
        if exc_status_code(e) == 422 and has_substring(e, _MOVE_GUARD_SUBSTRING):
            model.counters.tenant_move_conflict_rejected += 1
            logger.info("Guard G fired: deactivate({t}) rejected mid-move (HTTP 422)", t=tenant)
        else:
            findings.add(f"deactivate({tenant}) unexpected failure (expected 422+guard): {e!r}")


async def _activate(col: Any, tenant: str, model: Model, findings: Findings) -> None:
    try:
        await col.tenants.activate([tenant])
        model.tenant_status[tenant] = "ACTIVE"
        model.counters.activations += 1
    except Exception as e:
        # ACTIVATE is explicitly exempt from the move guard; any failure here is a FINDING.
        findings.add(f"activate({tenant}) failed but must never be guarded: {e!r}")
