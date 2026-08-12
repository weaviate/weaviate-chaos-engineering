"""Source-cluster bootstrap: namespaces, db users, roles, collections and the initial object set.

Nothing writes to the graduating namespace after this module returns: its clients are closed here and
only its API keys travel onward, and only to the target.
"""

import random
import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.data import DataObject

import wvclient
from config import Cluster, Config
from model import ExpectedObject, ExpectedSet
from restapi import Rest, poll

# Capability classes, decided at seed time and probed behaviourally on the target.
CAPABILITY_ADMIN = "wildcard-admin"
CAPABILITY_NARROW = "narrow-reader"

# Deliberately narrow, so the negative probe on the target has something real to be denied. Resources
# are short: a namespaced caller's role is stored as "{ns}:{role}" with "{ns}:*" resources
# (test/acceptance/authz/role_mgmt_test.go:95-108).
NARROW_PERMISSIONS = [
    {"action": "read_collections", "collections": {"collection": "*"}},
    {"action": "read_data", "data": {"collection": "*"}},
]

SEED_BATCH_SIZE = 100


class SeedError(Exception):
    """Seeding failed. Journey steps fail fast."""


@dataclass
class SeededUser:
    user_id: str
    short_id: str
    api_key: str
    capability: str


@dataclass
class SeededCollection:
    short_name: str
    qualified_name: str
    expected: ExpectedSet


@dataclass
class SeededNamespace:
    index: int
    name: str
    role_short: str
    role_qualified: str
    users: list[SeededUser] = field(default_factory=list)
    collections: list[SeededCollection] = field(default_factory=list)

    @property
    def admin_user(self) -> SeededUser:
        return self.users[0]


@dataclass
class SourceState:
    namespaces: list[SeededNamespace] = field(default_factory=list)

    @property
    def graduating(self) -> SeededNamespace:
        return self.namespaces[0]

    @property
    def neighbours(self) -> list[SeededNamespace]:
        return self.namespaces[1:]


def _observed(response: Any) -> str:
    """What a bounded retry saw. Never a hardcoded status: a poll's last observation is evidence."""
    return f"{response.status_code}: {response.text[:300]}"


def object_uuid(run_id: str, collection: str, serial: int) -> str:
    return str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, f"{run_id}/{collection}/{serial}"))


def object_properties(collection: str, serial: int) -> dict[str, Any]:
    return {
        "name": f"{collection}-{serial}",
        "payload": f"seeded object {serial} of {collection}",
        "seq": serial,
    }


async def seed_source(cfg: Config, root: Rest) -> SourceState:
    """Create every namespace, principal and object on the source. The graduating namespace is first."""
    state = SourceState()
    for index in range(1, cfg.namespace_count + 1):
        state.namespaces.append(await _seed_namespace(cfg, root, index))
    return state


async def _seed_namespace(cfg: Config, root: Rest, index: int) -> SeededNamespace:
    name = cfg.namespace_name(index)
    namespace = SeededNamespace(
        index=index,
        name=name,
        role_short=cfg.role_short_name(index),
        role_qualified=f"{name}:{cfg.role_short_name(index)}",
    )
    logger.info(f"seeding namespace {name}")

    # Names carry RUN_ID, so a 409 is a genuine collision — a reused RUN_ID, or a namespace of
    # that name still being deleted. The two causes are distinguishable only by a message this app
    # must not parse.
    response = await root.create_namespace(name)
    if response.status_code == 409:
        raise SeedError(
            f"namespace {name} already exists or is still deleting (409); "
            f"RUN_ID={cfg.run_id} appears to have been used before"
        )

    for serial in cfg.user_serials(index):
        namespace.users.append(await _create_user(cfg, root, name, serial))

    # Every write below is addressed to node 0. The assign handler's subject and role lookups are
    # leader queries (cluster/raft_rbac_query_endpoints.go:25-51; raft_dynuser_query_endpoints.go:
    # 23-45), so no node's lag can 404 them, but its authorization is decided by the serving node's
    # own casbin. Node 0 is the node the key poll in _create_user, the role create below and
    # wvclient.build_client(index=0) all address, so one node's enforcer decides every seed call.
    admin = namespace.admin_user
    # The built-in admin is a global role, so the operator may assign it: validateLocalRoleAssignment
    # only blocks roles carrying a namespace (handlers_authz.go:147-158).
    await _assign_role(cfg, root, admin.user_id, ["admin"])
    # The binding must be applied in RAFT before the admin acts. This poll proves that much and no
    # more; node 0's enforcer holding the binding is proven separately, by the 403 retry in
    # _create_role.
    await _await_role_visible(cfg, root, admin, "admin")

    async with principal_rest(cfg, cfg.source, admin) as admin_rest:
        await _create_role(cfg, admin_rest, namespace.role_short)
        # A namespace-local role can only be assigned by a caller confined to its namespace, so
        # this runs as the namespace admin and not as root: a global operator is refused by design,
        # which is what stops one namespace's role from reaching another's subjects
        # (validateLocalRoleAssignment, handlers_authz.go:147-158). Both references are short —
        # the handler qualifies them against the confined caller's namespace
        # (QualifyUserIDForLookup at handlers_authz.go:791, resolveAssignableRoles at :819) — and a
        # short name carries no namespace, so it passes the locality check by construction.
        for user in namespace.users[1:]:
            await _assign_role(cfg, admin_rest, user.short_id, [namespace.role_short])
            # The server stores the role qualified, and this read is issued as root, so the
            # qualified name is what comes back.
            await _await_role_visible(cfg, root, user, namespace.role_qualified)

    async with wvclient.connected(cfg.source, admin.api_key) as client:
        for serial in cfg.collection_serials(index):
            namespace.collections.append(await _seed_collection(cfg, client, name, serial))

    logger.success(
        f"seeded {name}: {len(namespace.users)} users, "
        f"{len(namespace.collections)} collections, role {namespace.role_qualified}"
    )
    return namespace


class principal_rest:
    """A short-lived Rest bound to one principal's key. Closed on every exit path."""

    def __init__(self, cfg: Config, cluster: Cluster, user: SeededUser):
        self._rest = Rest(
            cluster.http_base_urls,
            user.api_key,
            f"{cluster.label}/{user.user_id}",
            connect_timeout_s=cfg.rest_connect_timeout_s,
            read_timeout_s=cfg.rest_read_timeout_s,
        )

    async def __aenter__(self) -> Rest:
        return self._rest

    async def __aexit__(self, *exc: object) -> None:
        await self._rest.aclose()


async def _create_user(cfg: Config, root: Rest, namespace: str, serial: int) -> SeededUser:
    short_id = cfg.user_short_name(serial)
    user_id = f"{namespace}:{short_id}"

    async def attempt() -> tuple[bool, Any]:
        # A 422 here is raised before any RAFT command is issued
        # (db_users/handlers_db_users.go:429-431, ahead of CreateUser at :457), so no state can be
        # half-applied and this deliberate POST retry is safe. The most likely cause is a follower
        # that has not yet applied the namespace this run created seconds ago.
        response = await root.create_db_user(user_id)
        if response.status_code == 201:
            return True, response.json()["apikey"]
        return False, _observed(response)

    api_key = await poll(
        attempt,
        deadline_s=cfg.raft_visibility_timeout_s,
        interval_s=cfg.poll_interval_s,
        describe=f"db user {user_id} to be creatable",
    )

    user = SeededUser(
        user_id=user_id,
        short_id=short_id,
        api_key=api_key,
        capability=(CAPABILITY_ADMIN if _is_first_of_namespace(cfg, serial) else CAPABILITY_NARROW),
    )

    async with principal_rest(cfg, cfg.source, user) as user_rest:
        # Node-addressed, not rotating: authentication validates the key against the serving node's
        # own store (apikey/db_users.go:514-526), so this is one of the two reads whose answer
        # depends on which node serves it. The consumer of this key is
        # wvclient.build_client(index=0), which addresses node 0; a 200 from any other node would
        # prove nothing about the node serving the next call.
        node0 = user_rest.at(0)

        async def recognised() -> tuple[bool, Any]:
            # A freshly created key transiently 401s on a follower that has not applied it yet.
            response = await node0.own_info()
            return response.status_code == 200, response.status_code

        await poll(
            recognised,
            deadline_s=cfg.raft_visibility_timeout_s,
            interval_s=cfg.poll_interval_s,
            describe=f"key of {user_id} to be recognised",
        )
    return user


def _is_first_of_namespace(cfg: Config, serial: int) -> bool:
    return (serial - 1) % cfg.users_per_namespace == 0


async def _create_role(cfg: Config, admin_rest: Rest, role_short: str) -> None:
    """Create the namespace's custom role as its admin principal, retrying the node-local denial."""
    node0 = admin_rest.at(0)

    async def attempt() -> tuple[bool, Any]:
        # Authorization is decided by the serving node's own casbin (rbac/manager.go:752-770), which
        # may not yet hold the admin binding assigned moments ago. _await_role_visible is
        # leader-routed and cannot see that lag, so only this retry covers it. Retrying the POST is
        # safe: every 403 exit in createRole runs before the upsert (handlers_authz.go:376,379-381),
        # so no state is half-applied.
        response = await node0.create_role(role_short, NARROW_PERMISSIONS)
        return response.status_code == 201, _observed(response)

    await poll(
        attempt,
        deadline_s=cfg.raft_visibility_timeout_s,
        interval_s=cfg.poll_interval_s,
        describe=f"role {role_short} to be creatable by the namespace admin",
    )


async def _assign_role(cfg: Config, rest: Rest, user_id: str, roles: list[str]) -> None:
    """Assign roles on node 0, retrying the node-local denial for as long as a lag can explain it."""
    node0 = rest.at(0)

    async def attempt() -> tuple[bool, Any]:
        # Same rationale as the retry in _create_role: the caller's own grant is enforced by the
        # serving node's casbin (rbac/manager.go:752-770), which may not yet hold a binding written
        # moments ago, and every 403 exit runs before AddRolesForUser (handlers_authz.go:857), so
        # no state can be half-applied. A denial that outlives the deadline is not a lag — most
        # likely the caller is not confined to the role's namespace — and its body says so.
        response = await node0.assign_role(user_id, roles)
        return response.status_code == 200, _observed(response)

    await poll(
        attempt,
        deadline_s=cfg.raft_visibility_timeout_s,
        interval_s=cfg.poll_interval_s,
        describe=f"roles {roles} to be assignable to {user_id} by [{rest.label}]",
    )


async def _await_role_visible(cfg: Config, root: Rest, user: SeededUser, role: str) -> None:
    async def visible() -> tuple[bool, Any]:
        # A leader query (cluster/raft_rbac_query_endpoints.go:82-110), so one rotating read settles
        # the binding cluster-wide. It proves nothing about any node's enforcer; the 403 retry in
        # _create_role is what covers that.
        names = await root.roles_for_user(user.user_id)
        return role in names, names

    await poll(
        visible,
        deadline_s=cfg.raft_visibility_timeout_s,
        interval_s=cfg.poll_interval_s,
        describe=f"role {role} to be visible on {user.user_id}",
    )


async def _seed_collection(
    cfg: Config, client: Any, namespace: str, serial: int
) -> SeededCollection:
    short_name = cfg.collection_short_name(serial)
    seeded = SeededCollection(
        short_name=short_name,
        qualified_name=f"{namespace}:{short_name}",
        expected=ExpectedSet(collection=short_name),
    )
    # Short names auto-qualify server-side for a namespace-bound principal. The source caps the
    # replication factor at 1 regardless of what is asked for.
    await client.collections.create(
        name=short_name,
        properties=[
            Property(name="name", data_type=DataType.TEXT),
            Property(name="payload", data_type=DataType.TEXT),
            Property(name="seq", data_type=DataType.INT),
        ],
        vector_config=Configure.Vectors.self_provided(),
        replication_config=Configure.replication(factor=1),
    )

    collection = client.collections.use(short_name)
    rng = random.Random(f"{cfg.run_id}/{short_name}")
    pending: list[DataObject] = []
    expected_batch: list[ExpectedObject] = []
    for index in range(cfg.objects_per_collection):
        obj_uuid = object_uuid(cfg.run_id, short_name, index)
        properties = object_properties(short_name, index)
        vector = wvclient.random_vector(rng, cfg.vector_dim)
        pending.append(
            DataObject(
                properties=properties,
                uuid=obj_uuid,
                vector={wvclient.VECTOR_NAME: vector},
            )
        )
        expected_batch.append(
            ExpectedObject(
                uuid=obj_uuid,
                properties=properties,
                vectors={wvclient.VECTOR_NAME: vector},
            )
        )
        if len(pending) == SEED_BATCH_SIZE:
            await _insert_batch(collection, pending, expected_batch, seeded)
            pending, expected_batch = [], []
    if pending:
        await _insert_batch(collection, pending, expected_batch, seeded)

    actual = await wvclient.read_all(collection)
    diffs = seeded.expected.diff(actual)
    if diffs:
        raise SeedError(
            f"seed of {seeded.qualified_name} did not land: "
            + "; ".join(diff.render() for diff in diffs[:20])
        )
    logger.info(f"seeded {seeded.qualified_name} with {len(seeded.expected)} objects")
    return seeded


async def _insert_batch(
    collection: Any,
    pending: list[DataObject],
    expected_batch: list[ExpectedObject],
    seeded: SeededCollection,
) -> None:
    try:
        result = await collection.data.insert_many(pending)
    except Exception as exc:
        # An all-fail batch raises WeaviateInsertManyAllFailedError instead of returning.
        raise SeedError(_batch_failure(seeded, wvclient.usage_limit_rejection(exc), exc)) from exc
    # A partial failure returns normally, so the errors map is inspected on every batch.
    if result.has_errors:
        limit = wvclient.usage_limit_in_batch_errors(result.errors)
        raise SeedError(_batch_failure(seeded, limit, result.errors))
    for obj in expected_batch:
        seeded.expected.record_insert(obj)


def _batch_failure(seeded: SeededCollection, limit: str | None, detail: Any) -> str:
    if limit is not None:
        return (
            "the rig's MAXIMUM_ALLOWED_OBJECTS_COUNT is below what this run needs "
            f"(seeding {seeded.qualified_name}): {limit}"
        )
    return f"seeding {seeded.qualified_name} failed: {detail}"
