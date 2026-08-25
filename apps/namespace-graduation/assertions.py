"""The journey's verdict. Every assertion collects failures and never raises."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

import seed as seedmod
import wvclient
from config import Config
from load import NeighbourLoad
from model import ExpectedObject, ExpectedSet, normalise_vectors, vectors_equal
from restapi import Rest, dynamic_user_ids, poll
from seed import CAPABILITY_NARROW, SourceState

# usecases/auth/authorization/types.go:233-238
BUILT_IN_ROLES = frozenset({"viewer", "admin", "root", "read-only"})


@dataclass
class Failures:
    entries: list[tuple[str, str]] = field(default_factory=list)

    def add(self, assertion: str, detail: str) -> None:
        logger.error(f"FAIL [{assertion}] {detail}")
        self.entries.append((assertion, detail))

    def __bool__(self) -> bool:
        return bool(self.entries)

    def render(self) -> str:
        return "\n".join(f"  [{assertion}] {detail}" for assertion, detail in self.entries)


def _not_quiescent(f: Failures, assertion: str, load: NeighbourLoad) -> bool:
    if load.is_quiescent():
        return False
    f.add(assertion, "neighbour load is still running; a model comparison here would be a race")
    return True


async def assert_migrated_data_per_replica(
    cfg: Config, f: Failures, root: Rest, state: SourceState, load: NeighbourLoad
) -> None:
    """Every expected object is present like-for-like on every one of the three replicas."""
    assertion = "migrated-data-per-replica"
    if _not_quiescent(f, assertion, load):
        return
    # Each collection is swept inside its own guard: one collection's transport failure must not
    # leave the rest of the migration unverified.
    for seeded in state.graduating.collections:
        class_name = seeded.short_name
        try:
            if not await root.class_exists(class_name):
                f.add(assertion, f"migrated collection {class_name} is absent from the target")
                continue
            nodes = await _replica_nodes(cfg, f, root, class_name, assertion)
            if nodes is None:
                continue
            await _sweep_replicas(cfg, f, root, class_name, nodes, seeded.expected, assertion)
        except Exception as exc:
            f.add(assertion, f"{class_name} raised {exc!r}")


async def _replica_nodes(
    cfg: Config, f: Failures, root: Rest, class_name: str, assertion: str
) -> list[str] | None:
    shards = (await root.sharding_state(class_name)).get("shards") or []
    if not shards:
        f.add(assertion, f"{class_name} reports no shards on the target")
        return None
    under_replicated = {
        shard.get("shard"): len(shard.get("replicas") or [])
        for shard in shards
        if len(shard.get("replicas") or []) != cfg.target_replication_factor
    }
    if under_replicated:
        f.add(
            assertion,
            f"{class_name} shards not at rf={cfg.target_replication_factor}: {under_replicated}",
        )
        return None
    nodes = sorted({node for shard in shards for node in shard.get("replicas") or []})
    # The per-object sweep assumes every node hosts every shard, which holds for rf=3 on 3 nodes
    # and nowhere else.
    if len(nodes) != cfg.target_replication_factor:
        f.add(
            assertion,
            f"{class_name} replicas span {len(nodes)} nodes ({nodes}), "
            f"expected exactly {cfg.target_replication_factor}",
        )
        return None
    return nodes


async def _sweep_replicas(
    cfg: Config,
    f: Failures,
    root: Rest,
    class_name: str,
    nodes: list[str],
    expected: ExpectedSet,
    assertion: str,
) -> None:
    """One budget for the whole collection sweep, not per object.

    Unresolved ids are retried in later passes; when the budget expires every still-unresolved id is
    reported at once. A per-object deadline would multiply into hours at the default sizing.
    """
    snapshot = expected.snapshot()
    deadline = time.monotonic() + cfg.per_replica_sweep_timeout_s
    if not snapshot:
        # An empty model would make this assertion pass over nothing.
        f.add(assertion, f"{class_name} has an empty expected set; there is nothing to verify")
        return
    semaphore = asyncio.Semaphore(cfg.per_replica_sweep_concurrency)
    pending = [(node, uuid) for node in nodes for uuid in snapshot]

    async def check(node: str, uuid: str) -> tuple[tuple[str, str], str | None]:
        async with semaphore:
            response = await root.object_on_node(class_name, uuid, node)
        if response.status_code == 404:
            return (node, uuid), "absent"
        payload = response.json()
        want = snapshot[uuid]
        actual_properties = payload.get("properties") or {}
        # Two-sided: an extra property on a restored object is as much a corruption as a missing one.
        extra = sorted(set(actual_properties) - set(want.properties))
        if extra:
            return (node, uuid), f"unexpected properties {extra}"
        for name, value in want.properties.items():
            if actual_properties.get(name) != value:
                return (node, uuid), (
                    f"property {name}: expected {value!r}, got {actual_properties.get(name)!r}"
                )
        if not vectors_equal(want.vectors, normalise_vectors(payload)):
            return (node, uuid), "vector mismatch"
        return (node, uuid), None

    problems: dict[tuple[str, str], str] = {}
    while pending:
        # return_exceptions keeps one read's transport failure from abandoning the whole sweep; the
        # id stays unresolved and takes the same retry path as a mismatch.
        results = await asyncio.gather(
            *(check(node, uuid) for node, uuid in pending), return_exceptions=True
        )
        problems = {}
        for key, result in zip(pending, results):
            if isinstance(result, BaseException):
                problems[key] = f"read raised {result!r}"
            elif result[1] is not None:
                problems[key] = result[1]
        pending = list(problems)
        if not pending or time.monotonic() >= deadline:
            break
        await asyncio.sleep(cfg.poll_interval_s)

    for (node, uuid), reason in problems.items():
        f.add(assertion, f"{class_name} object {uuid} on node {node}: {reason}")
    if not problems:
        logger.success(
            f"{class_name}: {len(snapshot)} objects verified on each of {len(nodes)} replicas"
        )
    await _cross_check_counts(cfg, f, root, class_name, len(snapshot), deadline, assertion)


async def _cross_check_counts(
    cfg: Config,
    f: Failures,
    root: Rest,
    class_name: str,
    expected_count: int,
    deadline: float,
    assertion: str,
) -> None:
    """Per-node object counts converge asynchronously, so they share the sweep budget.

    The floor keeps a sweep that consumed its whole budget from collapsing this into a single
    attempt, which would report lag as a failure.
    """
    remaining = max(deadline - time.monotonic(), cfg.count_converge_floor_s)
    total_expected = expected_count * cfg.target_replication_factor

    async def counts() -> tuple[bool, Any]:
        per_node: dict[str, int] = {}
        for node in await root.nodes_for_class(class_name, cfg.rest_read_timeout_s * 2):
            per_node[node["name"]] = sum(
                shard.get("objectCount", 0)
                for shard in node.get("shards") or []
                if shard.get("class") == class_name
            )
        return (
            sum(per_node.values()) == total_expected
            and all(count <= expected_count for count in per_node.values()),
            per_node,
        )

    try:
        per_node = await poll(
            counts,
            deadline_s=remaining,
            interval_s=cfg.poll_interval_s,
            describe=f"per-node object counts of {class_name} to reach {total_expected}",
        )
        logger.info(f"{class_name} per-node counts: {per_node}")
    except Exception as exc:
        f.add(assertion, f"{class_name} per-node object counts never converged: {exc}")


async def assert_migrated_users_behave(
    cfg: Config, f: Failures, state: SourceState, load: NeighbourLoad
) -> None:
    """Every migrated user authenticates on the target with its source-issued key and behaves.

    Behavioural by design: no stored permission set, resource string or assignment table is
    compared, because those bind the test to strip internals rather than to the customer outcome.
    """
    assertion = "migrated-users-behave"
    if _not_quiescent(f, assertion, load):
        return
    migrated_class = state.graduating.collections[0].short_name
    for user in state.graduating.users:
        stripped = user.short_id
        try:
            await _probe_user(cfg, f, user, stripped, migrated_class, assertion)
        except Exception as exc:
            f.add(assertion, f"user {stripped}: probe raised {exc!r}")


async def _probe_user(
    cfg: Config,
    f: Failures,
    user: seedmod.SeededUser,
    stripped: str,
    migrated_class: str,
    assertion: str,
) -> None:
    rest = Rest(
        cfg.target.http_base_urls,
        user.api_key,
        f"target/{stripped}",
        connect_timeout_s=cfg.rest_connect_timeout_s,
        read_timeout_s=cfg.rest_read_timeout_s,
    )
    client = wvclient.build_client(cfg.target, user.api_key)
    try:
        # Authentication. The strip rewrites ids only, never key hashes or identifiers.
        response = await rest.own_info()
        if response.status_code != 200:
            f.add(
                assertion,
                f"user {stripped} source-issued key rejected on the target: "
                f"own-info {response.status_code}",
            )
            return
        await client.connect()

        # Positive probe: every migrated user can read, whatever its capability class. Its failure
        # is recorded and the capability probes still run: a user that cannot read may still hold
        # the wrong capability, and that second defect must not be hidden by the first.
        try:
            collection = client.collections.use(migrated_class)
            result = await collection.query.fetch_objects(limit=1)
            if not result.objects:
                f.add(assertion, f"user {stripped} read no object from {migrated_class}")
        except Exception as exc:
            f.add(assertion, f"user {stripped} could not read {migrated_class}: {exc!r}")

        # Capability probe. The namespace's first user arrives holding the built-in admin, and on a
        # namespaces-off target all built-ins carry wildcard policies (rbac/model.go:181-193), so
        # that user is a cluster-wide admin by design: it is probed positively, never negatively.
        # Derived in config.py, so the artefact summary and preflight name the same class.
        probe_class = cfg.probe_class_name(stripped)
        if user.capability == CAPABILITY_NARROW:
            await _expect_denied(f, client, probe_class, stripped, assertion)
        else:
            await _expect_permitted(f, client, probe_class, stripped, assertion)
    finally:
        await client.close()
        await rest.aclose()


async def _expect_denied(
    f: Failures, client: Any, probe_class: str, stripped: str, assertion: str
) -> None:
    """exists() is never used here: it catches everything and turns a denial into a bare False."""
    try:
        await client.collections.create(name=probe_class)
    except Exception as exc:
        if wvclient.is_permission_denied(exc):
            return
        f.add(assertion, f"narrow user {stripped} create denied with the wrong error: {exc!r}")
        return
    f.add(assertion, f"narrow user {stripped} was allowed to create collection {probe_class}")
    await _delete_probe(client, probe_class)


async def _expect_permitted(
    f: Failures, client: Any, probe_class: str, stripped: str, assertion: str
) -> None:
    try:
        await client.collections.create(name=probe_class)
    except Exception as exc:
        f.add(assertion, f"admin user {stripped} could not create collection: {exc!r}")
        return
    await _delete_probe(client, probe_class)


async def _delete_probe(client: Any, probe_class: str) -> None:
    """The probe collection is throwaway; a failed cleanup is logged, not a verdict."""
    try:
        await client.collections.delete(probe_class)
    except Exception as exc:
        logger.warning(f"could not delete probe collection {probe_class}: {exc!r}")


async def assert_target_user_and_role_sets(
    f: Failures, root: Rest, state: SourceState, load: NeighbourLoad
) -> None:
    """Exactly the migrated users and roles exist on the target. This is what makes leakage visible."""
    assertion = "target-user-and-role-sets"
    if _not_quiescent(f, assertion, load):
        return
    try:
        expected_users = {user.short_id for user in state.graduating.users}
        expected_roles = {state.graduating.role_short}
        # Both claims are cluster-wide and both reads are leader queries
        # (cluster/raft_query_endpoints.go:374-404), so one rotating read each answers from
        # authoritative state. db_env_user entries are the target's own static API-key users,
        # appended to every root-caller response and untouched by the restore.
        actual_users = set(dynamic_user_ids(await root.list_db_users()))
        if actual_users != expected_users:
            f.add(
                assertion,
                f"target dynamic users {sorted(actual_users)} != "
                f"migrated users {sorted(expected_users)}",
            )

        role_names = set(await root.list_roles())
        missing_built_ins = {"admin", "viewer"} - role_names
        if missing_built_ins:
            f.add(assertion, f"target is missing built-in roles {sorted(missing_built_ins)}")
        actual_roles = role_names - BUILT_IN_ROLES
        if actual_roles != expected_roles:
            f.add(
                assertion,
                f"target custom roles {sorted(actual_roles)} != "
                f"migrated roles {sorted(expected_roles)}",
            )
    except Exception as exc:
        f.add(assertion, f"raised {exc!r}")


async def assert_no_leakage_of_neighbour_collections(
    f: Failures, root: Rest, state: SourceState, load: NeighbourLoad
) -> None:
    """No neighbour-derived class name reaches the target, qualified or stripped."""
    assertion = "no-leakage"
    if _not_quiescent(f, assertion, load):
        return
    try:
        # The schema dump is a leader query with consistency on by default, so one rotating read is
        # the cluster's answer.
        target_classes = set(await root.schema_class_names())
        for namespace in state.neighbours:
            for seeded in namespace.collections:
                for name in (seeded.qualified_name, seeded.short_name):
                    if name in target_classes:
                        f.add(assertion, f"neighbour collection {name} appears on the target")
    except Exception as exc:
        f.add(assertion, f"raised {exc!r}")


async def assert_neighbour_integrity(
    cfg: Config, f: Failures, root: Rest, state: SourceState, load: NeighbourLoad, label: str
) -> None:
    """Neighbour namespaces are untouched. Run after restore and again after the source deletion."""
    assertion = f"neighbour-integrity/{label}"
    if _not_quiescent(f, assertion, load):
        return
    try:
        dynamic_users = set(dynamic_user_ids(await root.list_db_users()))
        role_names = set(await root.list_roles())
        for namespace in state.neighbours:
            if namespace.role_qualified not in role_names:
                f.add(assertion, f"neighbour role {namespace.role_qualified} is gone")
            for user in namespace.users:
                if user.user_id not in dynamic_users:
                    f.add(assertion, f"neighbour user {user.user_id} is gone")
                async with seedmod.principal_rest(cfg, cfg.source, user) as user_rest:
                    response = await user_rest.own_info()
                    if response.status_code != 200:
                        f.add(
                            assertion,
                            f"neighbour user {user.user_id} key no longer authenticates: "
                            f"own-info {response.status_code}",
                        )
            await _compare_neighbour_objects(cfg, f, namespace, assertion)
    except Exception as exc:
        f.add(assertion, f"raised {exc!r}")


async def _compare_neighbour_objects(
    cfg: Config, f: Failures, namespace: Any, assertion: str
) -> None:
    async with wvclient.connected(cfg.source, namespace.admin_user.api_key) as client:
        for seeded in namespace.collections:
            actual: dict[str, ExpectedObject] = await wvclient.read_all(
                client.collections.use(seeded.short_name)
            )
            diffs = seeded.expected.diff(actual)
            for diff in diffs[:20]:
                f.add(assertion, f"{namespace.name}: {diff.render()}")
            if len(diffs) > 20:
                f.add(assertion, f"{namespace.name}: {len(diffs) - 20} further divergences")
            if not diffs:
                logger.success(
                    f"[{assertion}] {namespace.name}:{seeded.short_name} intact "
                    f"({len(seeded.expected)} objects)"
                )


async def assert_source_post_state(
    cfg: Config, f: Failures, root: Rest, state: SourceState, load: NeighbourLoad
) -> None:
    """The graduating namespace and everything it owned are gone from the source."""
    assertion = "source-post-state"
    if _not_quiescent(f, assertion, load):
        return
    namespace = state.graduating.name
    prefix = f"{namespace}:"
    try:
        # All four reads are leader queries (cluster/raft_query_endpoints.go:374-404), so one
        # rotating read each is a cluster-wide fact.
        if await root.get_namespace(namespace) is not None:
            f.add(assertion, f"namespace {namespace} is still present on the source")
        leftover_users = [
            u for u in dynamic_user_ids(await root.list_db_users()) if u.startswith(prefix)
        ]
        if leftover_users:
            f.add(assertion, f"users of {namespace} are still present: {leftover_users}")
        leftover_roles = [r for r in await root.list_roles() if r.startswith(prefix)]
        if leftover_roles:
            f.add(assertion, f"roles of {namespace} are still present: {leftover_roles}")

        # The schema arm is polled as insurance only. The namespace 404 above already implies the
        # classes are gone: cleanup deletes users, aliases, classes and RBAC before removing the
        # entry, and the apply re-checks emptiness
        # (usecases/namespace_cleanup/coordinator.go:201-232).
        async def schema_clean() -> tuple[bool, Any]:
            names = [n for n in await root.schema_class_names() if n.startswith(prefix)]
            return not names, names

        try:
            await poll(
                schema_clean,
                deadline_s=cfg.raft_visibility_timeout_s,
                interval_s=cfg.poll_interval_s,
                describe=f"classes of {namespace} to disappear from the source schema",
            )
        except Exception as exc:
            f.add(assertion, str(exc))
    except Exception as exc:
        f.add(assertion, f"raised {exc!r}")
