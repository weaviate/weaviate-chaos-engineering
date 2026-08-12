"""The graduation itself: backup, restore, rf=1->3 scale-out, source-namespace deletion."""

from typing import Any

from loguru import logger

from config import Config
from restapi import (
    REPLICATION_CANCELLED,
    REPLICATION_IN_PROGRESS,
    REPLICATION_READY,
    Rest,
    backup_reached_success,
    poll,
)


class GraduationError(Exception):
    """A graduation step failed. Every step here fails fast."""


async def create_backup(cfg: Config, root: Rest, expected_classes: list[str]) -> str:
    """Wildcard-select the graduating namespace's classes, users and roles, and wait for SUCCESS.

    Driven by the global operator: backup create is denied to a namespace-bound principal.
    """
    namespace = cfg.graduating_namespace
    backup_id = cfg.backup_id
    # Pinned for the whole transaction: OnStatus answers from the coordinating node's in-memory
    # lastOp, while every other node answers from the object store's global descriptor, which is
    # only rewritten at phase transitions (usecases/backup/coordinator.go:460-479).
    transaction = root.pinned()
    body = {
        "id": backup_id,
        "include": [f"{namespace}:*"],
        "includeUsers": [f"{namespace}:*"],
        "includeRoles": [f"{namespace}:*"],
    }
    logger.info(f"creating backup {backup_id} on {transaction.pinned_url} with {body}")
    response = await transaction.backup_create(cfg.backup_backend, body)

    # The POST response is the only place the resolved wildcard selection appears; the status
    # response carries no classes field. A wildcard matching nothing is a server-side error
    # (usecases/backup/scheduler.go:769-771), so an empty migration cannot pass unnoticed.
    selected = sorted(response.get("classes") or [])
    if selected != sorted(expected_classes):
        raise GraduationError(
            f"backup {backup_id} selected {selected}, expected {sorted(expected_classes)}"
        )

    async def status() -> tuple[bool, Any]:
        # A status 404 raises: both operations write their global descriptor synchronously before
        # the POST returns (coordinator.go:222 and :301), so there is no not-yet-written window.
        payload = await transaction.backup_status(cfg.backup_backend, backup_id)
        return backup_reached_success(payload, f"backup {backup_id}"), payload.get("status")

    await poll(
        status,
        deadline_s=cfg.backup_timeout_s,
        interval_s=cfg.poll_interval_s,
        describe=f"backup {backup_id} to reach SUCCESS",
    )
    logger.success(f"backup {backup_id} succeeded with classes {selected}")
    return backup_id


async def restore_backup(cfg: Config, root: Rest, backup_id: str) -> None:
    """Restore onto the target with users and roles included, and wait for SUCCESS.

    Both options default to noRestore, which would migrate no users and no roles while still
    reporting SUCCESS. `include` is omitted deliberately: the backup already holds exactly the
    graduating namespace's classes, and restore-side include filtering happens pre-strip, so it
    would have to name the original qualified classes (usecases/backup/restorer.go:304-311).
    """
    transaction = root.pinned()
    body: dict[str, Any] = {"config": {"usersOptions": "all", "rolesOptions": "all"}}
    if cfg.restore_node_mapping:
        body["node_mapping"] = cfg.restore_node_mapping
    if cfg.restore_include:
        body["include"] = cfg.restore_include
    logger.info(f"restoring {backup_id} on {transaction.pinned_url} with {body}")
    await transaction.backup_restore(cfg.backup_backend, backup_id, body)

    async def status() -> tuple[bool, Any]:
        payload = await transaction.restore_status(cfg.backup_backend, backup_id)
        return backup_reached_success(payload, f"restore {backup_id}"), payload.get("status")

    await poll(
        status,
        deadline_s=cfg.restore_timeout_s,
        interval_s=cfg.poll_interval_s,
        describe=f"restore of {backup_id} to reach SUCCESS",
    )
    logger.success(f"restore of {backup_id} succeeded")


async def scale_out(cfg: Config, root: Rest, class_names: list[str]) -> None:
    """Scale each migrated collection to TARGET_REPLICATION_FACTOR, one collection at a time."""
    for class_name in class_names:
        await _scale_one(cfg, root, class_name)


async def _scale_one(cfg: Config, root: Rest, class_name: str) -> None:
    # Pre-apply gate. The server rejects an apply while any op for the collection is neither READY
    # nor CANCELLED (cluster/raft_replication_apply_endpoints.go:31-39) and surfaces that as an
    # opaque 500. Naming the blocking ops is what makes a re-run after an abort diagnosable.
    blocking = [
        (op.get("id"), (op.get("status") or {}).get("state"))
        for op in await root.replication_ops_for(class_name)
        if (op.get("status") or {}).get("state") not in (REPLICATION_READY, REPLICATION_CANCELLED)
    ]
    if blocking:
        raise GraduationError(
            f"cannot scale {class_name}: replication operations still in flight {blocking}"
        )

    response = await root.scale_plan(class_name, cfg.target_replication_factor)
    if response.status_code != 200:
        raise GraduationError(
            f"scale plan for {class_name} -> rf={cfg.target_replication_factor} returned "
            f"{response.status_code}: {response.text}"
        )
    plan = response.json()
    logger.info(f"applying scale plan for {class_name}: {plan}")

    # The plan is posted verbatim: the server rebuilds the actions from the body and echoes only
    # planId, so a reconstructed body would not round-trip.
    applied = await root.scale_apply(plan)
    operation_ids = [str(op_id) for op_id in applied.get("operationIds") or []]
    if not operation_ids:
        raise GraduationError(f"scale apply for {class_name} registered no operations: {applied}")

    for operation_id in operation_ids:

        async def ready(operation_id: str = operation_id) -> tuple[bool, Any]:
            details = await root.replication_details(operation_id)
            status = details.get("status") or {}
            state = status.get("state")
            errors = status.get("errors") or []
            if errors:
                # Diagnostic, never a verdict: ChangeState clears errors at every transition and
                # AddError records retried transients, so a healthy operation that retried once
                # carries them (cluster/replication/shard_replication_op_state.go:26,124-143).
                logger.warning(
                    f"replication operation {operation_id} of {class_name} in state {state} "
                    f"reports errors {errors}"
                )
            if state == REPLICATION_CANCELLED:
                raise GraduationError(
                    f"replication operation {operation_id} of {class_name} was cancelled: "
                    f"state={state} errors={errors}"
                )
            if state == REPLICATION_READY:
                return True, state
            if state not in REPLICATION_IN_PROGRESS:
                # Symmetric with the backup poll: an unrecognised state fails now rather than
                # spinning to the timeout.
                raise GraduationError(
                    f"replication operation {operation_id} of {class_name} reported unknown "
                    f"state {state!r}: {details}"
                )
            return False, state

        await poll(
            ready,
            deadline_s=cfg.scale_op_timeout_s,
            interval_s=cfg.poll_interval_s,
            describe=f"replication operation {operation_id} of {class_name} to reach READY",
        )

    async def replicated() -> tuple[bool, Any]:
        shards = (await root.sharding_state(class_name)).get("shards") or []
        counts = {shard.get("shard"): len(shard.get("replicas") or []) for shard in shards}
        done = bool(counts) and all(
            count == cfg.target_replication_factor for count in counts.values()
        )
        return done, counts

    counts = await poll(
        replicated,
        deadline_s=cfg.sharding_state_timeout_s,
        interval_s=cfg.poll_interval_s,
        describe=f"every shard of {class_name} to report {cfg.target_replication_factor} replicas",
    )
    # The apply issues per-shard replica commands and never touches the class definition
    # (cluster/raft_replication_apply_endpoints.go:78-121), so replicationConfig.factor still reads
    # 1 afterwards. rf is proven from sharding state alone.
    logger.success(f"{class_name} scaled out: {counts}")


async def delete_source_namespace(cfg: Config, root: Rest) -> None:
    """Delete the graduating namespace and wait for absence. Gone is a 404, not a state."""
    namespace = cfg.graduating_namespace
    await root.delete_namespace(namespace)

    async def absent() -> tuple[bool, Any]:
        current = await root.get_namespace(namespace)
        if current is None:
            return True, "absent"
        return False, current.get("state")

    await poll(
        absent,
        deadline_s=cfg.namespace_delete_timeout_s,
        interval_s=cfg.poll_interval_s,
        describe=f"namespace {namespace} to disappear",
    )
    # Cleanup runs on the RAFT leader's tick and the entry is not removed while anything it owns
    # remains, so a 404 implies the namespace's users, aliases, classes and RBAC rows are gone. The
    # read is a leader query (cluster/raft_query_endpoints.go:374-404), so that 404 is a
    # cluster-wide fact, whichever node this rotating poll addressed.
    logger.success(f"namespace {namespace} is gone from the source")
