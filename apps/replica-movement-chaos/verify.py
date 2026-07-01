"""Final assertion: drain -> settle -> activate-all -> CL.ALL read-back vs model
-> node-local per-replica reads -> backup SUCCESS. Any divergence is a FINDING with a precise diff.
"""

import asyncio
import random
from typing import Any

import httpx
import weaviate
from loguru import logger
from weaviate.classes.config import ConsistencyLevel

import topology
from backup import BackupState
from clients import Clients
from config import Config
from model import Findings, Model

_MAX_DIFF_SAMPLE = 10  # cap per-tenant diff lines so a systemic failure doesn't flood the logs
_NODE_READ_ATTEMPTS = 3  # authoritative per-node reads retry transient errors before failing closed
_NODE_READ_BACKOFF = 0.5


async def verify(
    clients: Clients,
    cfg: Config,
    model: Model,
    backup_state: BackupState,
    findings: Findings,
) -> None:
    coord = clients.coordinator
    await _drain(coord, cfg, model, findings)
    await _settle(coord, cfg, model, findings)
    await _activate_all(coord, cfg, findings)
    await _readback_cl_all(coord, cfg, model, findings)
    await _node_local(clients, cfg, model, findings)
    _assert_backup(cfg, backup_state, findings)


async def _drain(
    coord: weaviate.WeaviateAsyncClient, cfg: Config, model: Model, findings: Findings
) -> None:
    logger.info(
        "Drain: waiting for {n} inflight move(s) to reach terminal state", n=len(model.inflight)
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + cfg.drain_timeout
    while model.inflight:
        for shard, op_id in list(model.inflight.items()):
            if op_id == "PENDING":
                # A reservation whose replicate() never landed (worker cancelled mid-reserve);
                # nothing to poll, so drop it.
                logger.warning("Drain: dropping stale PENDING reservation for shard {s}", s=shard)
                model.inflight.pop(shard, None)
                continue
            try:
                op = await topology.get_replication_op(coord, op_id)
            except Exception as e:
                logger.warning("Drain: transient poll error op={op}: {e!r}", op=op_id, e=e)
                continue
            if topology.is_terminal(topology.op_state(op)):
                model.inflight.pop(shard, None)
        if not model.inflight:
            break
        if loop.time() > deadline:
            findings.add(
                f"drain timed out after {cfg.drain_timeout}s; stuck moves: {dict(model.inflight)}"
            )
            return
        await asyncio.sleep(cfg.move_poll_interval)
    logger.success("Drain complete: no inflight moves")


async def _settle(
    coord: weaviate.WeaviateAsyncClient, cfg: Config, model: Model, findings: Findings
) -> None:
    logger.info(
        "Settle: waiting for CL.ALL counts to match the model (budget {b}s)", b=cfg.settle_timeout
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + cfg.settle_timeout
    pending = set(cfg.tenant_names)
    while pending and loop.time() < deadline:
        for t in list(pending):
            expected = len(model.objects[t])
            actual = await _count(coord, cfg, t)
            if actual == expected:
                pending.discard(t)
        if pending:
            await asyncio.sleep(1)
    for t in pending:
        expected = len(model.objects[t])
        actual = await _count(coord, cfg, t)
        findings.add(f"settle timeout: tenant {t} CL.ALL count={actual} != expected {expected}")


async def _count(coord: weaviate.WeaviateAsyncClient, cfg: Config, tenant: str) -> int:
    ct = _cl_all(coord, cfg, tenant)
    try:
        return await ct.length()
    except Exception as e:
        logger.warning("count(tenant={t}) errored: {e!r}", t=tenant, e=e)
        return -1


async def _activate_all(
    coord: weaviate.WeaviateAsyncClient, cfg: Config, findings: Findings
) -> None:
    # The cluster is quiesced (no moves), so activation is unguarded and MUST succeed for every
    # tenant; this is the strict backstop distinguishing a broken activate from mid-chaos toggles.
    col = coord.collections.get(cfg.collection)
    try:
        await col.tenants.activate(cfg.tenant_names)
        logger.success("Activated all {n} tenants", n=len(cfg.tenant_names))
        return
    except Exception as e:
        logger.warning("Batch activate-all failed ({e!r}); retrying per-tenant to pinpoint", e=e)
    for t in cfg.tenant_names:
        try:
            await col.tenants.activate([t])
        except Exception as e:
            findings.add(
                f"verify activate-all: tenant {t} failed to activate on a quiesced cluster: {e!r}"
            )


async def _readback_cl_all(
    coord: weaviate.WeaviateAsyncClient, cfg: Config, model: Model, findings: Findings
) -> None:
    logger.info("Read-back: comparing CL.ALL contents against the model for all tenants")
    for t in cfg.tenant_names:
        expected = model.objects[t]
        actual: dict[str, dict[str, Any]] = {}
        ct = _cl_all(coord, cfg, t)
        try:
            async for obj in ct.iterator():
                actual[str(obj.uuid)] = obj.properties
        except Exception as e:
            findings.add(f"read-back tenant {t}: CL.ALL iterate failed: {e!r}")
            continue
        _compare_tenant(t, expected, actual, findings, where="CL.ALL")


async def _node_local(clients: Clients, cfg: Config, model: Model, findings: Findings) -> None:
    """AUTHORITATIVE per-node content check via raw REST ``?node_name=``.

    Unlike a CL.ALL read (masked by read-repair) or a pinned CL.ONE read (a coordinator may proxy
    it to a peer replica), ``GET /v1/objects/{Class}/{id}?node_name=<node>&tenant=<t>`` reads that
    node's LOCAL copy directly, so a divergent or missing replica cannot hide. Full tenant coverage
    by default; the per-node objectCount (verbose /v1/nodes) is only a fast pre-filter because it
    counts not-yet-compacted tombstones.
    """
    coord = clients.coordinator
    tenants = _node_local_tenants(cfg)
    try:
        state = await topology.sharding_state(coord, cfg.collection)
    except Exception as e:
        findings.add(f"node-local: could not read sharding state: {e!r}")
        return

    http_base = f"http://{cfg.nodes[0].http_host}:{cfg.nodes[0].http_port}"
    try:
        nodes_json = topology.fetch_nodes_verbose(http_base)
    except Exception as e:
        logger.warning(
            "node-local: /v1/nodes?output=verbose failed; skipping count pre-filter: {e!r}", e=e
        )
        nodes_json = {}

    origins = {spec.name: f"http://{spec.http_host}:{spec.http_port}" for spec in cfg.nodes}
    sem = asyncio.Semaphore(cfg.node_local_concurrency)
    async with httpx.AsyncClient(timeout=30.0) as http:
        for t in tenants:
            replicas = state.get(t, [])
            if not replicas:
                findings.add(f"node-local: tenant {t} has no replicas in sharding state")
                continue
            await _verify_tenant_nodes(
                http, sem, origins, nodes_json, cfg, t, replicas, model, findings
            )


def _node_local_tenants(cfg: Config) -> list[str]:
    n = cfg.node_local_sample
    if n and n < len(cfg.tenant_names):
        logger.info(
            "Node-local: sampling {n} of {tot} tenants (NODE_LOCAL_SAMPLE speed knob)",
            n=n,
            tot=len(cfg.tenant_names),
        )
        return random.sample(cfg.tenant_names, n)
    logger.info(
        "Node-local: FULL per-node content coverage over {n} tenants", n=len(cfg.tenant_names)
    )
    return list(cfg.tenant_names)


async def _verify_tenant_nodes(
    http: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    origins: dict[str, str],
    nodes_json: dict[str, Any],
    cfg: Config,
    tenant: str,
    replicas: list[str],
    model: Model,
    findings: Findings,
) -> None:
    expected = model.objects[tenant]
    oids = list(expected.keys())
    for node in replicas:
        origin = origins.get(node)
        if origin is None:
            findings.add(f"node-local: no HTTP origin for replica node {node} (tenant {tenant})")
            continue
        raw = topology.node_shard_object_count(nodes_json, node, tenant)
        if raw is not None and raw != len(expected):
            logger.warning(
                "node-local pre-filter: node={node} tenant={t} objectCount={raw} != model {exp}",
                node=node,
                t=tenant,
                raw=raw,
                exp=len(expected),
            )
        results = await asyncio.gather(
            *(_get_object_on_node(http, sem, origin, node, cfg, tenant, oid) for oid in oids)
        )
        _assert_node_reads(tenant, node, oids, expected, results, findings)


async def _get_object_on_node(
    http: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    origin: str,
    node: str,
    cfg: Config,
    tenant: str,
    oid: str,
) -> tuple[int, dict[str, Any] | None]:
    """Read one object from a SPECIFIC node's local storage. Returns (status, properties):
    200 + properties on hit, (404, None) if absent on that node, (-1/5xx, None) on read failure."""
    url = f"{origin}/v1/objects/{cfg.collection}/{oid}"
    params = {"node_name": node, "tenant": tenant}
    async with sem:
        for attempt in range(_NODE_READ_ATTEMPTS):
            try:
                resp = await http.get(url, params=params)
            except httpx.HTTPError:
                if attempt + 1 == _NODE_READ_ATTEMPTS:
                    return -1, None
                await asyncio.sleep(_NODE_READ_BACKOFF * (attempt + 1))
                continue
            if resp.status_code == 200:
                return 200, resp.json().get("properties") or {}
            if resp.status_code == 404:
                return 404, None
            # Fail closed on a persistent server error: the cluster is quiesced at verify time, so a
            # read we cannot complete must not be silently treated as agreement.
            if attempt + 1 == _NODE_READ_ATTEMPTS:
                return resp.status_code, None
            await asyncio.sleep(_NODE_READ_BACKOFF * (attempt + 1))
    return -1, None


def _assert_node_reads(
    tenant: str,
    node: str,
    oids: list[str],
    expected: dict[str, dict[str, Any]],
    results: list[tuple[int, dict[str, Any] | None]],
    findings: Findings,
) -> None:
    missing: list[str] = []
    diverged: list[tuple[str, Any, Any]] = []
    errored: list[tuple[str, int]] = []
    for oid, (status, props) in zip(oids, results):
        if status == 200:
            if not _payload_equal(expected[oid], props or {}):
                diverged.append((oid, expected[oid], props))
        elif status == 404:
            missing.append(oid)
        else:
            errored.append((oid, status))
    if missing or diverged:
        findings.add(
            f"node-local CONTENT mismatch tenant={tenant} node={node}: "
            f"missing={missing[:_MAX_DIFF_SAMPLE]} "
            f"diverged={[(o, e, a) for o, e, a in diverged[:_MAX_DIFF_SAMPLE]]}"
        )
    if errored:
        findings.add(
            f"node-local read errors tenant={tenant} node={node} "
            f"(could not authoritatively verify): {errored[:_MAX_DIFF_SAMPLE]}"
        )


def _compare_tenant(
    tenant: str,
    expected: dict[str, dict[str, Any]],
    actual: dict[str, dict[str, Any]],
    findings: Findings,
    *,
    where: str,
) -> None:
    exp_keys = set(expected.keys())
    act_keys = set(actual.keys())
    missing = exp_keys - act_keys
    extra = act_keys - exp_keys
    diverged = []
    for oid in exp_keys & act_keys:
        if not _payload_equal(expected[oid], actual[oid]):
            diverged.append((oid, expected[oid], actual[oid]))
    if missing or extra or diverged:
        findings.add(
            f"{where} mismatch tenant={tenant}: "
            f"missing={sorted(missing)[:_MAX_DIFF_SAMPLE]} "
            f"extra={sorted(extra)[:_MAX_DIFF_SAMPLE]} "
            f"diverged={[(o, e, a) for o, e, a in diverged[:_MAX_DIFF_SAMPLE]]}"
        )


def _payload_equal(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if str(expected.get("payload")) != str(actual.get("payload")):
        return False
    try:
        return int(expected.get("seq")) == int(actual.get("seq"))
    except (TypeError, ValueError):
        return expected.get("seq") == actual.get("seq")


def _assert_backup(cfg: Config, backup_state: BackupState, findings: Findings) -> None:
    if not cfg.backup_enabled:
        logger.info("Backup disabled; skipping backup assertion")
        return
    if backup_state.status == "SUCCESS":
        logger.success("Backup {bid} succeeded", bid=backup_state.backup_id)
    else:
        findings.add(
            f"backup did not succeed: id={backup_state.backup_id} "
            f"status={backup_state.status} error={backup_state.error}"
        )


def _cl_all(coord: weaviate.WeaviateAsyncClient, cfg: Config, tenant: str) -> Any:
    return (
        coord.collections.get(cfg.collection)
        .with_tenant(tenant)
        .with_consistency_level(ConsistencyLevel.ALL)
    )
