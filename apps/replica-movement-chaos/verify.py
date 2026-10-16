"""Final assertion: drain -> settle -> activate-all -> node-local per-replica reads (AUTHORITATIVE,
repair-free) -> CL.ALL read-back (secondary) -> backup SUCCESS -> backup restore + no-lost-shard.
Node-local runs BEFORE any CL.ALL object read so a divergent replica is observed un-repaired (F2).
Any divergence is a FINDING with a precise diff.
"""

import asyncio
import random
from typing import Any

import httpx
import weaviate
from loguru import logger
from weaviate.classes.config import ConsistencyLevel
from weaviate.collections import CollectionAsync

import topology
from backup import BackupState, restore_backup
from clients import Clients
from config import Config
from model import Findings, Model

_MAX_DIFF_SAMPLE = 10  # cap per-tenant diff lines so a systemic failure doesn't flood the logs
_NODE_READ_ATTEMPTS = 3  # authoritative per-node reads retry transient errors before failing closed
_NODE_READ_BACKOFF = 0.5
_CONVERGE_POLL = 1.0  # seconds between convergence re-polls of a still-mismatched read


async def verify(
    clients: Clients,
    cfg: Config,
    model: Model,
    backup_state: BackupState,
    findings: Findings,
) -> None:
    coord = clients.coordinator
    await _drain(coord, cfg, model, findings)
    # Bound the expensive content phases so a degraded cluster fails LOUD (a FINDING) instead of
    # hanging verify forever. Drain has its own budget; the backup assertion below is pure/local.
    try:
        await asyncio.wait_for(
            _verify_content(clients, cfg, model, findings), timeout=cfg.verify_timeout
        )
    except asyncio.TimeoutError:
        findings.add(f"verify did not complete within budget ({cfg.verify_timeout}s)")
    _assert_backup(cfg, backup_state, findings)
    # DESTRUCTIVE (drops + recreates the collection), so it runs dead last, after every live-data
    # assertion above has already read the original data.
    await _verify_backup_restore(clients, cfg, backup_state, findings)


async def _verify_content(clients: Clients, cfg: Config, model: Model, findings: Findings) -> None:
    coord = clients.coordinator
    await _settle(coord, cfg, model, findings)
    await _activate_all(coord, cfg, findings)
    # Node-local (repair-free ?node_name=) MUST precede the CL.ALL read-back: a CL.ALL object read
    # triggers Weaviate read-repair and would heal a divergent replica before the authoritative check
    # could observe it (F2). _settle above is safe first because its CL.ALL .length() is a COUNT, not
    # a per-object value read, so it cannot value-repair a torn object.
    await _node_local(clients, cfg, model, findings)
    # Secondary, non-authoritative signal: full-tenant CL.ALL enumeration still catches EXTRA/orphaned
    # objects that node-local (which only probes the model's expected ids) structurally cannot see.
    await _readback_cl_all(coord, cfg, model, findings)


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
    sem = asyncio.Semaphore(cfg.verify_concurrency)
    pending = set(cfg.tenant_names)
    while pending and loop.time() < deadline:
        counts = await asyncio.gather(*(_count_bounded(coord, cfg, sem, t) for t in pending))
        for t, actual in counts:
            if actual == len(model.objects[t]):
                pending.discard(t)
        if pending:
            await asyncio.sleep(1)
    for t in pending:
        actual = await _count(coord, cfg, t)
        findings.add(
            f"settle timeout: tenant {t} CL.ALL count={actual} != expected {len(model.objects[t])}"
        )


async def _count_bounded(
    coord: weaviate.WeaviateAsyncClient, cfg: Config, sem: asyncio.Semaphore, tenant: str
) -> tuple[str, int]:
    async with sem:
        return tenant, await _count(coord, cfg, tenant)


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
    col = coord.collections.use(cfg.collection)
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
    # Bounded per-tenant fan-out: at most verify_concurrency tenants' object sets are materialised
    # at once, so the read-back never holds all tenants' objects in memory simultaneously.
    sem = asyncio.Semaphore(cfg.verify_concurrency)
    # return_exceptions so one tenant's unexpected raise becomes a FINDING instead of cancelling the
    # siblings and losing their diffs.
    results = await asyncio.gather(
        *(_readback_tenant(coord, cfg, sem, model, t, findings) for t in cfg.tenant_names),
        return_exceptions=True,
    )
    _report_gather_errors(cfg.tenant_names, results, findings, where="read-back")
    # Completeness summary: every tenant is always checked (the gather above), so report the
    # pass/fail tally, not only the failures. A clean tenant returns True; a failed one returns False
    # or (an exception captured by the gather) — either way it lands in `failed`.
    failed = [t for t, r in zip(cfg.tenant_names, results) if r is not True]
    ok = len(cfg.tenant_names) - len(failed)
    if failed:
        logger.error(
            "Read-back: {ok}/{n} OK, FAILED: {f}", ok=ok, n=len(cfg.tenant_names), f=failed
        )
    else:
        logger.success("Read-back: {ok}/{n} OK, FAILED: []", ok=ok, n=len(cfg.tenant_names))


async def _readback_tenant(
    coord: weaviate.WeaviateAsyncClient,
    cfg: Config,
    sem: asyncio.Semaphore,
    model: Model,
    tenant: str,
    findings: Findings,
) -> bool:
    """Return True if the tenant read back clean, False if it failed — for the completeness tally.
    Findings are still added on failure; the return value only drives the summary count."""
    async with sem:
        expected = model.objects[tenant]
        actual = await _read_tenant_cl_all(coord, cfg, tenant)
        if actual is None:
            findings.add(f"read-back tenant {tenant}: CL.ALL iterate failed")
            return False
        missing, extra, diverged = _diff_tenant(expected, actual)
        if missing or extra or diverged:
            # Counts already matched at settle, so a diff here may just be a value still reconciling;
            # re-poll before declaring a FINDING (F7: clears only on a fresh clean CL.ALL read).
            missing, extra, diverged = await _converge_readback(
                coord, cfg, tenant, expected, missing, extra, diverged
            )
        _report_diff(tenant, missing, extra, diverged, findings, where="CL.ALL")
        return not (missing or extra or diverged)


async def _read_tenant_cl_all(
    coord: weaviate.WeaviateAsyncClient, cfg: Config, tenant: str
) -> dict[str, dict[str, Any]] | None:
    """Full CL.ALL snapshot of a tenant as {uuid -> properties}, or None if the iterate failed."""
    actual: dict[str, Any] = {}
    ct = _cl_all(coord, cfg, tenant)
    try:
        async for obj in ct.iterator():
            actual[str(obj.uuid)] = obj.properties
    except Exception as e:
        logger.warning("read-back tenant {t}: CL.ALL iterate failed: {e!r}", t=tenant, e=e)
        return None
    return actual


async def _converge_readback(
    coord: weaviate.WeaviateAsyncClient,
    cfg: Config,
    tenant: str,
    expected: dict[str, dict[str, Any]],
    missing: set[str],
    extra: set[str],
    diverged: list[tuple[str, Any, Any]],
) -> tuple[set[str], set[str], list[tuple[str, Any, Any]]]:
    """Re-poll a read-back mismatch by re-reading the tenant at CL.ALL and re-diffing, bounded by
    NODE_LOCAL_CONVERGE_TIMEOUT. A mismatch clears ONLY when a fresh SUCCESSFUL full read shows no
    diff for the tracked oids; a read error never clears (F7 — a permanent divergence must not be
    waited out into a pass). Returns the surviving (missing, extra, diverged)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + cfg.node_local_converge_timeout
    logger.info(
        "read-back converge: tenant={t} re-polling (missing={m} extra={e} diverged={d}, budget {b}s)",
        t=tenant,
        m=len(missing),
        e=len(extra),
        d=len(diverged),
        b=cfg.node_local_converge_timeout,
    )
    while (missing or extra or diverged) and loop.time() < deadline:
        await asyncio.sleep(_CONVERGE_POLL)
        actual = await _read_tenant_cl_all(coord, cfg, tenant)
        if actual is None:
            continue  # a re-read error is not a match; leave the mismatch in place
        missing, extra, diverged = _diff_tenant(expected, actual)
    return missing, extra, diverged


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
    # Two bounds (B4): tenant_sem caps how many tenants are resident (and materialising their
    # oids/results) at once; read_sem caps TOTAL in-flight GETs across all those tenants, so
    # tenant-parallelism cannot blow up connection/memory pressure.
    tenant_sem = asyncio.Semaphore(cfg.verify_concurrency)
    read_sem = asyncio.Semaphore(cfg.node_local_concurrency)
    async with httpx.AsyncClient(timeout=30.0) as http:
        results = await asyncio.gather(
            *(
                _verify_tenant_bounded(
                    tenant_sem, http, read_sem, origins, nodes_json, cfg, t, state, model, findings
                )
                for t in tenants
            ),
            return_exceptions=True,
        )
    _report_gather_errors(tenants, results, findings, where="node-local")


async def _verify_tenant_bounded(
    tenant_sem: asyncio.Semaphore,
    http: httpx.AsyncClient,
    read_sem: asyncio.Semaphore,
    origins: dict[str, str],
    nodes_json: dict[str, Any],
    cfg: Config,
    tenant: str,
    state: dict[str, list[str]],
    model: Model,
    findings: Findings,
) -> None:
    async with tenant_sem:
        replicas = state.get(tenant, [])
        if not replicas:
            findings.add(f"node-local: tenant {tenant} has no replicas in sharding state")
            return
        await _verify_tenant_nodes(
            http, read_sem, origins, nodes_json, cfg, tenant, replicas, model, findings
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
        by_oid = dict(zip(oids, results))
        # Counts already matched at settle, so a missing/diverged read here may be a replica still
        # reconciling; re-poll only those before declaring a FINDING (F7: clears only on a fresh
        # ?node_name= read that actually matches).
        await _converge_node_reads(http, sem, origin, node, cfg, tenant, expected, by_oid)
        _assert_node_reads(tenant, node, expected, by_oid, findings)


async def _converge_node_reads(
    http: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    origin: str,
    node: str,
    cfg: Config,
    tenant: str,
    expected: dict[str, dict[str, Any]],
    by_oid: dict[str, tuple[int, dict[str, Any] | None]],
) -> None:
    """Re-poll ONLY the still-mismatched node-local reads for (tenant, node) until each heals or the
    NODE_LOCAL_CONVERGE_TIMEOUT budget expires. An oid clears ONLY on a fresh authoritative
    ?node_name= read that returns 200 AND matches the model; a 404, divergent value, or read error
    leaves it mismatched (F7 — a real divergence is never waited out into a pass). ``by_oid`` is
    updated in place with the freshest observation so the eventual FINDING reports current state."""
    pending = [oid for oid, (s, p) in by_oid.items() if not _read_matches(expected[oid], s, p)]
    if not pending:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + cfg.node_local_converge_timeout
    logger.info(
        "node-local converge: tenant={t} node={n} re-polling {k} mismatch(es) (budget {b}s)",
        t=tenant,
        n=node,
        k=len(pending),
        b=cfg.node_local_converge_timeout,
    )
    while pending and loop.time() < deadline:
        await asyncio.sleep(_CONVERGE_POLL)
        results = await asyncio.gather(
            *(_get_object_on_node(http, sem, origin, node, cfg, tenant, oid) for oid in pending)
        )
        still: list[str] = []
        for oid, res in zip(pending, results):
            by_oid[oid] = res
            if not _read_matches(expected[oid], *res):
                still.append(oid)
        pending = still


def _read_matches(expected_obj: dict[str, Any], status: int, props: dict[str, Any] | None) -> bool:
    """A node-local read confirms the model ONLY on a 200 whose payload matches; a 404, 5xx or read
    error is never a match, so it can never CLEAR a convergence mismatch (F7)."""
    return status == 200 and _payload_equal(expected_obj, props or {})


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
    expected: dict[str, dict[str, Any]],
    by_oid: dict[str, tuple[int, dict[str, Any] | None]],
    findings: Findings,
) -> None:
    missing: list[str] = []
    diverged: list[tuple[str, Any, Any]] = []
    errored: list[tuple[str, int]] = []
    for oid, (status, props) in by_oid.items():
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


def _diff_tenant(
    expected: dict[str, dict[str, Any]], actual: dict[str, dict[str, Any]]
) -> tuple[set[str], set[str], list[tuple[str, Any, Any]]]:
    exp_keys = set(expected.keys())
    act_keys = set(actual.keys())
    missing = exp_keys - act_keys
    extra = act_keys - exp_keys
    diverged = [
        (oid, expected[oid], actual[oid])
        for oid in exp_keys & act_keys
        if not _payload_equal(expected[oid], actual[oid])
    ]
    return missing, extra, diverged


def _report_diff(
    tenant: str,
    missing: set[str],
    extra: set[str],
    diverged: list[tuple[str, Any, Any]],
    findings: Findings,
    *,
    where: str,
) -> None:
    if missing or extra or diverged:
        findings.add(
            f"{where} mismatch tenant={tenant}: "
            f"missing={sorted(missing)[:_MAX_DIFF_SAMPLE]} "
            f"extra={sorted(extra)[:_MAX_DIFF_SAMPLE]} "
            f"diverged={[(o, e, a) for o, e, a in diverged[:_MAX_DIFF_SAMPLE]]}"
        )


def _report_gather_errors(
    tenants: list[str], results: list[Any], findings: Findings, *, where: str
) -> None:
    """Turn any per-tenant exception captured by a return_exceptions gather into a FINDING (never
    silently swallowed). A CancelledError is re-raised so an outer verify-timeout cancel is honoured
    rather than masked as a per-tenant finding."""
    for tenant, r in zip(tenants, results):
        if isinstance(r, asyncio.CancelledError):
            raise r
        if isinstance(r, BaseException):
            findings.add(f"{where} tenant {tenant} raised unexpectedly: {r!r}")


def _payload_equal(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if str(expected.get("payload")) != str(actual.get("payload")):
        return False
    try:
        return int(expected.get("seq")) == int(actual.get("seq"))  # pyright: ignore
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


def _cl_all(coord: weaviate.WeaviateAsyncClient, cfg: Config, tenant: str) -> CollectionAsync:
    return (
        coord.collections.use(cfg.collection)
        .with_tenant(tenant)
        .with_consistency_level(ConsistencyLevel.ALL)
    )


def _cl_one(coord: weaviate.WeaviateAsyncClient, cfg: Config, tenant: str) -> CollectionAsync:
    return (
        coord.collections.use(cfg.collection)
        .with_tenant(tenant)
        .with_consistency_level(ConsistencyLevel.ONE)
    )


async def _verify_backup_restore(
    clients: Clients, cfg: Config, backup_state: BackupState, findings: Findings
) -> None:
    """FINAL phase (C8/F8): restore the one-shot backup into the ORIGINAL collection and assert NO
    WHOLESALE SHARD LOSS vs the backup-start snapshot. A MOVE deleting a source shard mid-backup can
    yield a backup that reports SUCCESS yet captured incomplete shard files, restoring to
    missing/garbage data; asserting only the create-status cannot catch that. DESTRUCTIVE (drops then
    recreates the live collection), so it must run after every live-data assertion.

    Robust lower-bound property, NOT object-equality (to avoid re-introducing flakiness): a tenant is
    a FINDING only if ABSENT or grossly short (below BACKUP_RESTORE_MIN_FRACTION of its snapshot),
    tolerating writes/deletes that raced the backup. Runs (never warns-and-skips) whenever backup
    reached SUCCESS and restore is enabled; skips only when backup/restore is disabled."""
    if not (cfg.backup_enabled and cfg.backup_restore_enabled):
        logger.info("Restore verification disabled (backup/restore off); skipping")
        return
    if backup_state.status != "SUCCESS":
        # Nothing to restore. _assert_backup already FINDINGed the non-SUCCESS create (backup is
        # enabled here), so the run is already red; skipping cannot open an F8 false-pass.
        logger.warning(
            "Restore verify skipped: backup status={s} (not SUCCESS)", s=backup_state.status
        )
        return

    coord = clients.coordinator
    logger.info(
        "Restore verify: dropping collection {c} then restoring backup {bid}",
        c=cfg.collection,
        bid=backup_state.backup_id,
    )
    try:
        await coord.collections.delete(cfg.collection)
    except Exception as e:
        findings.add(f"restore verify: failed to drop collection before restore: {e!r}")
        return
    assert backup_state.backup_id is not None, "backup_id must be set for a SUCCESS backup"
    status = await restore_backup(coord, cfg, backup_state.backup_id)
    if status != "SUCCESS":
        findings.add(
            f"restore verify: backup {backup_state.backup_id} did not restore SUCCESS (status={status})"
        )
        return
    # This runs OUTSIDE verify()'s verify_timeout (restore must proceed even after a content-phase
    # timeout), so the count-read loop has no other overall ceiling; bound it here so a degraded
    # cluster fails LOUD (a FINDING) instead of hanging. The restore itself is already bounded by
    # restore_timeout.
    try:
        await asyncio.wait_for(
            _assert_restored_counts(coord, cfg, backup_state.backup_snapshot, findings),
            timeout=cfg.restore_verify_timeout,
        )
    except asyncio.TimeoutError:
        findings.add(
            f"restore verify: restored-count check did not complete within budget "
            f"({cfg.restore_verify_timeout}s); could not confirm no shard loss (F8)"
        )


async def _assert_restored_counts(
    coord: weaviate.WeaviateAsyncClient,
    cfg: Config,
    snapshot: dict[str, int],
    findings: Findings,
) -> None:
    col = coord.collections.use(cfg.collection)
    try:
        restored_tenants = set(await col.tenants.get())
    except Exception as e:
        findings.add(f"restore verify: could not list tenants on the restored collection: {e!r}")
        return
    targets = [t for t in cfg.tenant_names if snapshot.get(t, 0) > 0]
    # A snapshot>0 tenant missing from the restored collection is a lost shard, decided from
    # membership alone (no count needed, so a read error can't be mistaken for "absent").
    for t in (t for t in targets if t not in restored_tenants):
        findings.add(
            f"restore verify: tenant {t} ABSENT after restore (snapshot={snapshot[t]}); "
            "a MOVE may have deleted a source shard mid-backup (F8)"
        )
    present = [t for t in targets if t in restored_tenants]
    if present:
        await _activate_restored(col, present)
    sem = asyncio.Semaphore(cfg.verify_concurrency)
    counts = await asyncio.gather(
        *(_restored_count_bounded(coord, cfg, sem, t) for t in present),
        return_exceptions=True,
    )
    for t, res in zip(present, counts):
        if isinstance(res, asyncio.CancelledError):
            raise res
        if isinstance(res, BaseException):
            findings.add(f"restore verify: tenant {t} count raised unexpectedly: {res!r}")
            continue
        # max(1, ...): a snapshot>0 tenant that restored EMPTY is always a lost shard, but for a tiny
        # snapshot (e.g. 1) the fraction rounds the floor to 0 and res==0 would escape (0<0 false).
        # Flooring at 1 catches that; STRICTER only (no-op at the 1000-object default, floor=500).
        floor = max(1, int(cfg.backup_restore_min_fraction * snapshot[t]))
        if res < floor:
            findings.add(
                f"restore verify: tenant {t} grossly short after restore: restored={res} < "
                f"{cfg.backup_restore_min_fraction:.2f}*{snapshot[t]}={floor} (lost shard, F8)"
            )


async def _activate_restored(col: CollectionAsync, present: list[str]) -> None:
    """Best-effort batch activate of restored tenants so their per-tenant CL.ONE counts are readable.
    A bounded retry only REDUCES flakiness: a transient activate failure would leave tenants INACTIVE
    and under-report the count -> a false-POSITIVE F8 finding (red, safe direction). On ultimate
    failure this still returns and the caller's count read runs UNCHANGED (reads low -> FINDING); it
    must never signal the caller to skip the count, which would open an F8 false-PASS."""
    for attempt in range(_NODE_READ_ATTEMPTS):
        try:
            await col.tenants.activate(present)
            return
        except Exception as e:
            if attempt + 1 == _NODE_READ_ATTEMPTS:
                logger.warning(
                    "restore verify: batch activate of restored tenants failed after {n} "
                    "attempts ({e!r}); counts may under-report",
                    n=_NODE_READ_ATTEMPTS,
                    e=e,
                )
                return
            await asyncio.sleep(_NODE_READ_BACKOFF * (attempt + 1))


async def _restored_count_bounded(
    coord: weaviate.WeaviateAsyncClient, cfg: Config, sem: asyncio.Semaphore, tenant: str
) -> int:
    async with sem:
        return await _restored_count(coord, cfg, tenant)


async def _restored_count(coord: weaviate.WeaviateAsyncClient, cfg: Config, tenant: str) -> int:
    """CL.ONE count of a restored tenant. CL.ONE (not ALL) is the robust lower-bound read: a genuine
    backup-file loss is missing on EVERY replica so it still reads low, but a replica still loading
    its restored shard cannot falsely lower the count. Retries only a transient read ERROR (never a
    successful low count) so a real loss is FINDINGed immediately, never waited out (F8)."""
    ct = _cl_one(coord, cfg, tenant)
    for attempt in range(_NODE_READ_ATTEMPTS):
        try:
            return await ct.length()
        except Exception as e:
            if attempt + 1 == _NODE_READ_ATTEMPTS:
                logger.warning("restore count(tenant={t}) errored: {e!r}", t=tenant, e=e)
                return -1
            await asyncio.sleep(_NODE_READ_BACKOFF * (attempt + 1))
    return -1
