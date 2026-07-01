"""Collection creation + CL.ALL seed of the authoritative model."""

from typing import Any

import weaviate
from loguru import logger
from weaviate.classes.config import ConsistencyLevel, Configure, DataType, Property
from weaviate.classes.data import DataObject
from weaviate.classes.tenants import Tenant
from weaviate.util import generate_uuid5

from config import Config
from model import Model

SEED_CHUNK = 200


def object_id(tenant: str, idx: int) -> str:
    """Deterministic id so every retry of the same logical object is byte-identical."""
    return generate_uuid5(f"{tenant}-{idx}")


def seed_payload(tenant: str, idx: int) -> dict[str, Any]:
    return {"payload": f"seed-{tenant}-{idx}", "seq": idx}


async def create_collection(coord: weaviate.WeaviateAsyncClient, cfg: Config) -> None:
    if await coord.collections.exists(cfg.collection):
        logger.info("Collection {c} already exists; deleting for a clean run", c=cfg.collection)
        await coord.collections.delete(cfg.collection)
    await coord.collections.create(
        name=cfg.collection,
        properties=[
            Property(name="payload", data_type=DataType.TEXT),
            Property(name="seq", data_type=DataType.INT),
        ],
        multi_tenancy_config=Configure.multi_tenancy(
            enabled=True,
            auto_tenant_creation=True,
            auto_tenant_activation=True,
        ),
        replication_config=Configure.replication(factor=cfg.rf),
        vector_config=Configure.Vectors.self_provided(),
    )
    logger.info(
        "Created collection {c} (rf={rf}, MT+auto-create+auto-activate, self-provided vectors)",
        c=cfg.collection,
        rf=cfg.rf,
    )


async def create_tenants(coord: weaviate.WeaviateAsyncClient, cfg: Config) -> None:
    col = coord.collections.get(cfg.collection)
    await col.tenants.create([Tenant(name=t) for t in cfg.tenant_names])
    logger.info("Created {n} tenants", n=len(cfg.tenant_names))


async def seed(coord: weaviate.WeaviateAsyncClient, cfg: Config, model: Model) -> None:
    for t in cfg.tenant_names:
        ct = (
            coord.collections.get(cfg.collection)
            .with_tenant(t)
            .with_consistency_level(ConsistencyLevel.ALL)
        )
        for start in range(0, cfg.objects_per_tenant, SEED_CHUNK):
            chunk = [
                (object_id(t, idx), seed_payload(t, idx))
                for idx in range(start, min(start + SEED_CHUNK, cfg.objects_per_tenant))
            ]
            await _insert_chunk(ct, chunk, tenant=t)
            for oid, payload in chunk:
                model.objects[t][oid] = payload
        model._next_idx[t] = cfg.objects_per_tenant
        model.counters.seeded += cfg.objects_per_tenant
        logger.info("Seeded tenant {t}: {n} objects at CL.ALL", t=t, n=cfg.objects_per_tenant)


async def _insert_chunk(ct: Any, chunk: list[tuple[str, dict[str, Any]]], *, tenant: str) -> None:
    objs = [DataObject(properties=p, uuid=oid) for oid, p in chunk]
    for attempt in range(10):
        res = await ct.data.insert_many(objs)
        if not res.has_errors:
            return
        logger.warning(
            "Seed chunk for tenant {t} had errors (attempt {a}); retrying idempotently: {e}",
            t=tenant,
            a=attempt + 1,
            e=res.errors,
        )
    raise RuntimeError(f"Failed to seed a chunk for tenant {tenant} after 10 attempts")
