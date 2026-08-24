"""Entrypoint: `python3 run.py preflight` checks the rig, `python3 run.py journey` runs the test."""

import asyncio
import sys
from typing import Awaitable, TypeVar

from loguru import logger

import assertions
import restapi
import wvclient
from config import Cluster, Config, ConfigError
from graduate import create_backup, delete_source_namespace, restore_backup, scale_out
from load import NeighbourLoad
from restapi import Rest, RestError, dynamic_user_ids, static_user_ids
from seed import SourceState, seed_source

MODES = ("preflight", "journey")

T = TypeVar("T")


class PreflightError(Exception):
    """The rig is misconfigured. Every problem found is named, with the knob that fixes it."""


def _rest(cfg: Config, cluster: Cluster) -> Rest:
    return Rest(
        cluster.http_base_urls,
        cluster.root_api_key,
        f"{cluster.label}/root",
        connect_timeout_s=cfg.rest_connect_timeout_s,
        read_timeout_s=cfg.rest_read_timeout_s,
    )


async def _checked(problems: list[str], check: str, call: Awaitable[T]) -> T | None:
    """Turn an unexpected status into a named preflight problem instead of a traceback.

    Every probe here reaches a live cluster over the network, so any of them can answer 500 or, on a
    misconfigured key, 401/403.
    """
    try:
        return await call
    except RestError as exc:
        problems.append(f"{check} could not be checked: {exc}")
        return None


async def preflight(cfg: Config, src_root: Rest, tgt_root: Rest) -> None:
    """Read-only rig validation. Creates nothing on either cluster."""
    problems: list[str] = []
    # A name no collection has, used to make the replica-movement handler answer. Distinct from the
    # capability-probe classes, whose absence is checked below.
    scale_probe_class = f"{cfg.collection_prefix}Probe{cfg.run_id.capitalize()}"
    probe_user = f"probe{cfg.run_id}"

    # Reachability of every configured base URL, unauthenticated.
    for cluster in (cfg.source, cfg.target):
        for base_url in cluster.http_base_urls:
            reason = await restapi.ready(
                base_url,
                connect_timeout_s=cfg.rest_connect_timeout_s,
                read_timeout_s=cfg.rest_read_timeout_s,
            )
            if reason is not None:
                problems.append(f"{cluster.label} {base_url} is not ready: {reason}")
    if problems:
        raise PreflightError("; ".join(problems))

    # Version and backup module on both clusters.
    for cluster, root in ((cfg.source, src_root), (cfg.target, tgt_root)):
        meta = await _checked(problems, f"{cluster.label} /v1/meta", root.meta())
        if meta is None:
            continue
        if f"backup-{cfg.backup_backend}" not in (meta.get("modules") or {}):
            problems.append(
                f"{cluster.label} has no backup-{cfg.backup_backend} module — check ENABLE_MODULES"
            )

    # The namespace flags. A 404 on the list endpoint means namespaces are off.
    source_namespaces = await _checked(
        problems, "source /v1/namespaces", src_root.list_namespaces()
    )
    if source_namespaces is not None and source_namespaces.status_code != 200:
        problems.append("source cluster has namespaces disabled — set NAMESPACES_ENABLED=true")
    target_namespaces = await _checked(
        problems, "target /v1/namespaces", tgt_root.list_namespaces()
    )
    if target_namespaces is not None and target_namespaces.status_code != 404:
        problems.append(
            "target cluster has namespaces enabled, so the restore would not strip them — "
            "unset NAMESPACES_ENABLED on the target"
        )

    # Replica movement. 501 is the only failing status: it is what the disabled stub answers
    # (adapters/handlers/rest/replication/handlers_setup.go:35-38). Every other status means the
    # live handler ran, which is all this probe asks. 500 included: against a class that does not
    # exist the leader query's NotFound surfaces as `failed to execute query: rpc error: code =
    # NotFound desc = could not get replication scale plan: class not found: <Class>`, observed on
    # a healthy rig.
    scale_probe = await _checked(
        problems,
        "target replica movement",
        tgt_root.scale_plan(scale_probe_class, 1),
    )
    if scale_probe is not None:
        if scale_probe.status_code == 501:
            problems.append(
                "target has replica movement disabled — set REPLICA_MOVEMENT_ENABLED=true"
            )
        else:
            logger.info(
                f"target replica movement probe returned {scale_probe.status_code}: "
                f"{scale_probe.text}"
            )

    # RBAC. With RBAC off the role restore is skipped silently while the restore still reports
    # SUCCESS (adapters/handlers/rest/configure_api.go:761-767; usecases/backup/restorer.go:180).
    target_roles = await _checked(problems, "target RBAC roles", tgt_root.list_roles())
    if target_roles is not None:
        missing = {"admin", "viewer"} - set(target_roles)
        if missing:
            problems.append(
                f"target is missing built-in roles {sorted(missing)} — enable RBAC "
                "(AUTHORIZATION_RBAC_ENABLED) on the target"
            )

    # Db users. The same nil-guard skips the user restore silently when disabled; the list
    # endpoint cannot detect it, since it answers 200 with an empty array.
    db_user_probe = await _checked(problems, "target db users", tgt_root.get_db_user(probe_user))
    if db_user_probe is not None and db_user_probe.status_code == 422:
        problems.append(
            "target has dynamic db users disabled — set AUTHENTICATION_DB_USERS_ENABLED=true"
        )

    # Consent for the destructive restore.
    target_users = await _checked(problems, "target user store", tgt_root.list_db_users())
    if target_users is not None and target_roles is not None:
        existing_dynamic = dynamic_user_ids(target_users)
        existing_custom_roles = sorted(set(target_roles) - assertions.BUILT_IN_ROLES)
        if (existing_dynamic or existing_custom_roles) and not cfg.allow_target_store_replacement:
            problems.append(
                "the restore will DELETE the target's dynamic users "
                f"{sorted(existing_dynamic)} and custom roles {existing_custom_roles}; "
                "set ALLOW_TARGET_STORE_REPLACEMENT=true to proceed"
            )

    # The strip refuses when a stripped user id lands on a static API-key user
    # (usecases/auth/authorization/rbac/namespace_strip.go:243-245), after a full seed and backup
    # have been spent.
    if target_users is not None:
        collisions = set(static_user_ids(target_users)) & set(cfg.graduating_user_short_names())
        if collisions:
            problems.append(
                f"stripped user ids {sorted(collisions)} collide with the target's static API-key "
                "users; the restore would refuse — change RUN_ID or the static user names"
            )

    # Node names and count.
    source_nodes = await _checked(problems, "source /v1/nodes", src_root.nodes())
    target_nodes = await _checked(problems, "target /v1/nodes", tgt_root.nodes())
    if target_nodes is not None and len(target_nodes) != cfg.target_replication_factor:
        problems.append(
            f"target has {len(target_nodes)} nodes ({target_nodes}), "
            f"expected {cfg.target_replication_factor}"
        )
    # A subset test, not equality: the restore only needs every node the backup descriptor names to
    # resolve on the target (usecases/backup/coordinator.go:537-542). Target nodes the backup does
    # not name take no part in it, which is this rig's shape: source [node1], target [node1, node2,
    # node3].
    if source_nodes is not None and target_nodes is not None:
        unresolvable = sorted(set(source_nodes) - set(target_nodes))
        if unresolvable and not cfg.restore_node_mapping:
            mapping = dict(zip(unresolvable, sorted(target_nodes)))
            problems.append(
                f"source nodes {unresolvable} do not exist on the target "
                f"{sorted(target_nodes)}, so the restore would fail to resolve them; "
                f"set RESTORE_NODE_MAPPING, e.g. {mapping}"
            )

    # Class names carry RUN_ID, so a hit means RUN_ID was reused — or that a previous run's probe
    # cleanup failed, which is why the capability-probe classes are in the set.
    target_classes = await _checked(problems, "target /v1/schema", tgt_root.schema_class_names())
    if target_classes is not None:
        this_run = set(cfg.graduating_collection_short_names()) | set(cfg.probe_class_names())
        reused = sorted(set(target_classes) & this_run)
        if reused:
            problems.append(f"target already holds this run's classes {reused} — RUN_ID was reused")

    # Backend liveness. The module check above only proves the module is compiled in.
    for cluster, root in ((cfg.source, src_root), (cfg.target, tgt_root)):
        response = await _checked(
            problems,
            f"{cluster.label} {cfg.backup_backend} backend",
            root.list_backups(cfg.backup_backend, cfg.rest_read_timeout_s * 2),
        )
        if response is None:
            continue
        if response.status_code != 200:
            problems.append(
                f"{cluster.label} cannot enumerate the {cfg.backup_backend} backend "
                f"({response.status_code}: {response.text}) — check BACKUP_S3_ENDPOINT, "
                "BACKUP_S3_BUCKET and the credentials"
            )

    # Client reachability, gRPC included. skip_init_checks=False makes connect() run the
    # unauthenticated gRPC health check, so an unpublished gRPC port fails here, not mid-seed.
    for cluster in (cfg.source, cfg.target):
        try:
            async with wvclient.connected(cluster, cluster.root_api_key, skip_init_checks=False):
                logger.info(f"{cluster.label} client connected over HTTP and gRPC")
        except Exception as exc:
            problems.append(
                f"{cluster.label} client could not connect ({exc!r}) — check the HTTP and gRPC "
                "ports the compose file publishes"
            )

    if problems:
        raise PreflightError("\n".join(f"  - {problem}" for problem in problems))
    logger.success("preflight passed")


def log_artefact_summary(cfg: Config, state: SourceState | None, backup_id: str | None) -> None:
    """Reachable on every path, including an abort — the runs that leave the most debris."""
    logger.info("--- artefacts created by this run ---")
    logger.info(f"RUN_ID={cfg.run_id} backup_id={backup_id}")
    if state is None:
        logger.info("no namespaces were seeded")
    else:
        for namespace in state.namespaces:
            logger.info(
                f"source namespace {namespace.name}: "
                f"users {[u.user_id for u in namespace.users]}, "
                f"role {namespace.role_qualified}, "
                f"collections {[c.qualified_name for c in namespace.collections]}"
            )
        logger.info(
            "source cleanup: DELETE /v1/namespaces/<ns> for each neighbour above, polled to 404"
        )
    # The capability probes create real classes on the target, so a failed probe cleanup leaves
    # classes the collection list above does not cover.
    logger.info(
        "target cleanup: DELETE /v1/schema/<Class> for "
        f"{cfg.graduating_collection_short_names()}, plus any surviving capability-probe class of "
        f"{cfg.probe_class_names()}"
    )


async def journey(cfg: Config, src_root: Rest, tgt_root: Rest) -> int:
    # Pre-initialised so the artefact summary in the inner finally is reachable from every abort,
    # including one part-way through seeding.
    state: SourceState | None = None
    load: NeighbourLoad | None = None
    backup_id: str | None = None
    failures = assertions.Failures()

    try:
        state = await seed_source(cfg, src_root)
        load = NeighbourLoad(cfg, state)
        await load.start()
        graduating_classes = [c.qualified_name for c in state.graduating.collections]
        backup_id = await create_backup(cfg, src_root, graduating_classes)
        await restore_backup(cfg, tgt_root, backup_id)
        async with load.paused():
            await assertions.assert_neighbour_integrity(
                cfg, failures, src_root, state, load, "after-restore"
            )
        await scale_out(cfg, tgt_root, [c.short_name for c in state.graduating.collections])
        await delete_source_namespace(cfg, src_root)
    finally:
        # The summary has its own finally: stop_and_drain() re-raises a dead load task's exception,
        # and the abort it reports is exactly when the operator needs the inventory of what this run
        # left on each cluster.
        try:
            if load is not None:
                await load.stop_and_drain()
        finally:
            log_artefact_summary(cfg, state, backup_id)

    # Reaching here means every journey step succeeded, so state and load are set.

    await assertions.assert_migrated_data_per_replica(cfg, failures, tgt_root, state, load)
    await assertions.assert_migrated_users_behave(cfg, failures, state, load)
    await assertions.assert_target_user_and_role_sets(failures, tgt_root, state, load)
    await assertions.assert_no_leakage_of_neighbour_collections(failures, tgt_root, state, load)
    await assertions.assert_neighbour_integrity(
        cfg, failures, src_root, state, load, "after-ns-delete"
    )
    await assertions.assert_source_post_state(cfg, failures, src_root, state, load)

    if failures:
        logger.error(f"{len(failures.entries)} assertion failures:\n{failures.render()}")
        return 1
    logger.success("namespace graduation verified")
    return 0


async def main(mode: str) -> int:
    cfg = Config.from_env()
    logger.info(f"mode={mode} config={cfg.summary()}")
    src_root, tgt_root = _rest(cfg, cfg.source), _rest(cfg, cfg.target)
    try:
        await preflight(cfg, src_root, tgt_root)
        if mode == "preflight":
            return 0
        return await journey(cfg, src_root, tgt_root)
    finally:
        await src_root.aclose()
        await tgt_root.aclose()


if __name__ == "__main__":
    selected = sys.argv[1] if len(sys.argv) > 1 else "journey"
    if selected not in MODES:
        logger.error(f"unknown mode {selected!r}, expected one of {MODES}")
        sys.exit(2)
    try:
        sys.exit(asyncio.run(main(selected)))
    except (ConfigError, PreflightError) as error:
        logger.error(str(error))
        sys.exit(1)
    except Exception as error:
        logger.exception(f"journey aborted: {error!r}")
        sys.exit(1)
