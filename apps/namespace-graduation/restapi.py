"""Authenticated async REST over httpx, plus the polling primitive every wait in this app uses.

The weaviate client covers no namespace, backup, replication, user or role endpoint, so this layer
carries all of them. Control flow keys on status codes only: namespace lifecycle errors all render as
the single opaque string "instance unavailable" (usecases/namespaces/public_message.go:21-34), so
server message text is never parsed.
"""

import asyncio
import time
from typing import Any, Awaitable, Callable, Iterable, Sequence

import httpx
from loguru import logger

# Bounded schedule for the idempotent-only retry policy below. Deliberately not an env tunable:
# it parametrises a policy that lives in this module and nowhere else.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_S = 1.0
_RETRY_METHODS = frozenset({"GET", "DELETE"})
_RETRY_STATUSES = frozenset({502, 503})

# entities/backup/status.go:16-25. FINALIZING is on the restore happy path; note the wire spelling
# asymmetry: CANCELLING has two Ls, CANCELED has one.
BACKUP_IN_PROGRESS = frozenset({"STARTED", "TRANSFERRING", "TRANSFERRED", "FINALIZING"})
BACKUP_SUCCESS = "SUCCESS"
BACKUP_TERMINAL_FAILURE = frozenset({"FAILED", "CANCELLING", "CANCELED"})

# ReplicationReplicateDetailsReplicaStatus.state. CANCELLED is the only terminal failure; the
# status.errors rationale lives with the operation poll that acts on it, in graduate.py.
REPLICATION_READY = "READY"
REPLICATION_CANCELLED = "CANCELLED"
REPLICATION_IN_PROGRESS = frozenset(
    {"REGISTERED", "HYDRATING", "FINALIZING", "INTEGRATING", "DEHYDRATING"}
)


class _AnyStatus:
    """Sentinel for `expect`: return whatever the server answered, so the caller classifies it."""


ANY_STATUS = _AnyStatus()


class RestError(Exception):
    """A response outside the caller's expected status set. Carries the server's body verbatim."""

    def __init__(self, label: str, method: str, path: str, status: int, body: str):
        self.label = label
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"[{label}] {method} {path} -> {status}: {body}")


class PollTimeout(Exception):
    """A bounded wait expired. Names what was awaited and the last observation."""


class BackupStateError(Exception):
    """A backup or restore reached a terminal failure state, or reported an unknown one."""


async def poll(
    fn: Callable[[], Awaitable[tuple[bool, Any]]],
    *,
    deadline_s: float,
    interval_s: float,
    describe: str,
) -> Any:
    """Await fn() until it reports done. fn returns (done, observation); raising from fn fails fast."""
    deadline = time.monotonic() + deadline_s
    observation: Any = None
    while True:
        done, observation = await fn()
        if done:
            return observation
        if time.monotonic() >= deadline:
            raise PollTimeout(
                f"timed out after {deadline_s:g}s waiting for {describe}; "
                f"last observation: {observation!r}"
            )
        await asyncio.sleep(interval_s)


def backup_reached_success(payload: dict[str, Any], describe: str) -> bool:
    """Classify one status payload against the eight-value enum. No status is merely tolerated."""
    status = payload.get("status")
    if status == BACKUP_SUCCESS:
        return True
    if status in BACKUP_IN_PROGRESS:
        return False
    if status in BACKUP_TERMINAL_FAILURE:
        raise BackupStateError(f"{describe} reached terminal state {status}: {payload}")
    raise BackupStateError(f"{describe} reported unknown state {status!r}: {payload}")


class Rest:
    """One principal's REST access to one cluster.

    The owner holds a single httpx.AsyncClient, created lazily inside the running loop and closed by
    aclose(). pinned() and at() clones share the owner's client and never close it.
    """

    def __init__(
        self,
        base_urls: Sequence[str],
        api_key: str,
        label: str,
        *,
        connect_timeout_s: float,
        read_timeout_s: float,
    ):
        if not base_urls:
            raise ValueError(f"[{label}] needs at least one base URL")
        self.label = label
        self._base_urls = list(base_urls)
        self._api_key = api_key
        self._connect_timeout_s = connect_timeout_s
        self._read_timeout_s = read_timeout_s
        self._owner: "Rest | None" = None
        self._client: httpx.AsyncClient | None = None
        self._cursor = 0
        self._pin: int | None = None
        self._addressed = False

    # --- transport -----------------------------------------------------------

    def _clone(self, suffix: str, pin: int) -> "Rest":
        owner = self._owner or self
        clone = Rest(
            self._base_urls,
            self._api_key,
            f"{self.label}@{suffix}",
            connect_timeout_s=self._connect_timeout_s,
            read_timeout_s=self._read_timeout_s,
        )
        clone._owner = owner
        clone._pin = pin
        return clone

    def pinned(self) -> "Rest":
        """A clone bound to one base URL, sharing this instance's client and pool."""
        owner = self._owner or self
        return self._clone("pinned", owner._cursor % len(self._base_urls))

    def at(self, index: int) -> "Rest":
        """A clone bound to base_urls[index], sharing this instance's client and pool.

        Used where the serving node's own state decides the outcome: authentication against a
        node's key store, and RBAC enforcement by a node's casbin — role creation and role
        assignment alike, both of which authorize the caller against local policy. Management reads
        are leader-routed and rotate instead. Unlike pinned(), this clone never re-pins: the
        identity of the node is the point, so a dead endpoint fails the call outright.
        """
        clone = self._clone(f"node{index}", index % len(self._base_urls))
        clone._addressed = True
        return clone

    @property
    def pinned_url(self) -> str | None:
        return None if self._pin is None else self._base_urls[self._pin]

    async def aclose(self) -> None:
        if self._owner is not None:
            return
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        owner = self._owner or self
        if owner._client is None:
            # trust_env=False mirrors the weaviate client (client-ref/weaviate/config.py:118): an
            # inherited proxy variable must not reroute traffic aimed at the host's published ports.
            owner._client = httpx.AsyncClient(
                headers={"authorization": f"Bearer {owner._api_key}"},
                trust_env=False,
            )
        return owner._client

    def _next_url(self) -> str:
        if self._pin is not None:
            return self._base_urls[self._pin]
        url = self._base_urls[self._cursor % len(self._base_urls)]
        self._cursor += 1
        return url

    def _advance(self, reason: str) -> None:
        # A node-addressed clone never moves: rotating it away would answer the poll from a node
        # the caller is not asking about.
        if self._pin is None or self._addressed:
            return
        self._pin = (self._pin + 1) % len(self._base_urls)
        logger.warning(
            f"[{self.label}] re-pinned to {self._base_urls[self._pin]} after {reason}; "
            "status now reads from the object store's global descriptor"
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        expect: Iterable[int] | _AnyStatus = (200,),
        read_timeout_s: float | None = None,
    ) -> httpx.Response:
        expected = None if isinstance(expect, _AnyStatus) else frozenset(expect)
        timeout = httpx.Timeout(
            connect=self._connect_timeout_s,
            write=self._connect_timeout_s,
            pool=self._connect_timeout_s,
            read=self._read_timeout_s if read_timeout_s is None else read_timeout_s,
        )
        # Only idempotent methods are retried. A POST that reached the server may have applied
        # (openapi-specs/schema.json:10302-10307 documents 503 as "may or may not have been
        # applied"), and every read-side exception below is raised after the request was written.
        attempts = _RETRY_ATTEMPTS if method in _RETRY_METHODS else 1
        for attempt in range(1, attempts + 1):
            url = self._next_url()
            try:
                response = await self._http().request(
                    method, f"{url}{path}", json=json, params=params, timeout=timeout
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if attempt == attempts:
                    raise
                logger.warning(f"[{self.label}] {method} {path} on {url}: {exc!r}, retrying")
                self._advance(repr(exc))
                await asyncio.sleep(_RETRY_BACKOFF_S * attempt)
                continue
            if expected is not None and response.status_code in expected:
                return response
            if response.status_code in _RETRY_STATUSES and attempt < attempts:
                logger.warning(
                    f"[{self.label}] {method} {path} on {url} -> {response.status_code}, retrying"
                )
                self._advance(f"status {response.status_code}")
                await asyncio.sleep(_RETRY_BACKOFF_S * attempt)
                continue
            # ANY_STATUS is handled after the retry branch, so a probe still benefits from the
            # 502/503 retry rather than reporting a transient as its result.
            if expected is None:
                return response
            raise RestError(self.label, method, path, response.status_code, response.text)
        raise AssertionError("unreachable: the retry loop returns or raises on its last attempt")

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)

    # --- cluster ------------------------------------------------------------

    async def meta(self) -> dict[str, Any]:
        return (await self.get("/v1/meta")).json()

    async def nodes(self) -> list[str]:
        payload = (await self.get("/v1/nodes")).json()
        return [node["name"] for node in payload.get("nodes", [])]

    async def nodes_for_class(self, class_name: str, read_timeout_s: float) -> list[dict[str, Any]]:
        response = await self.get(
            f"/v1/nodes/{class_name}",
            params={"output": "verbose"},
            read_timeout_s=read_timeout_s,
        )
        return response.json().get("nodes", [])

    async def schema_class_names(self) -> list[str]:
        payload = (await self.get("/v1/schema")).json()
        return [cls["class"] for cls in payload.get("classes") or []]

    async def class_exists(self, class_name: str) -> bool:
        response = await self.get(f"/v1/schema/{class_name}", expect=(200, 404))
        return response.status_code == 200

    # --- namespaces ---------------------------------------------------------

    async def create_namespace(self, namespace: str) -> httpx.Response:
        """201 on create; 409 means the name exists or is still being deleted."""
        return await self.post(f"/v1/namespaces/{namespace}", expect=(201, 409))

    async def get_namespace(self, namespace: str) -> dict[str, Any] | None:
        """None when the namespace is gone: gone is absence, never a state."""
        response = await self.get(f"/v1/namespaces/{namespace}", expect=(200, 404))
        return None if response.status_code == 404 else response.json()

    async def list_namespaces(self) -> httpx.Response:
        """404 on this endpoint means namespaces are disabled on this cluster."""
        return await self.get("/v1/namespaces", expect=(200, 404))

    async def delete_namespace(self, namespace: str) -> None:
        await self.delete(f"/v1/namespaces/{namespace}", expect=(202,))

    # --- users and roles ----------------------------------------------------

    async def create_db_user(self, user_id: str) -> httpx.Response:
        """201 carries the new key. 422 is raised before any RAFT command, so a retry is safe."""
        return await self.post(f"/v1/users/db/{user_id}", expect=(201, 422))

    async def get_db_user(self, user_id: str) -> httpx.Response:
        """422 means db users are disabled (db_users/handlers_db_users.go:233-234)."""
        return await self.get(f"/v1/users/db/{user_id}", expect=(200, 404, 422))

    async def list_db_users(self) -> list[dict[str, Any]]:
        """To a root caller this appends every static API-key user, tagged db_env_user.

        Callers must filter on dbUserType; see dynamic_user_ids and static_user_ids.
        """
        return (await self.get("/v1/users/db")).json()

    async def own_info(self) -> httpx.Response:
        return await self.get("/v1/users/own-info", expect=(200, 401, 403))

    async def create_role(self, name: str, permissions: list[dict[str, Any]]) -> httpx.Response:
        """201 on create. A 403 is decided by the serving node's own casbin
        (rbac/manager.go:752-770) and is raised before the upsert
        (handlers_authz.go:376,379-381), so a retry is safe.
        """
        return await self.post(
            "/v1/authz/roles", json={"name": name, "permissions": permissions}, expect=(201, 403)
        )

    async def list_roles(self) -> list[str]:
        payload = (await self.get("/v1/authz/roles")).json()
        return [role["name"] for role in payload or []]

    async def assign_role(self, user_id: str, roles: list[str]) -> httpx.Response:
        """200 on assign. A 403 has two causes, both raised before AddRolesForUser
        (handlers_authz.go:857): the serving node's own casbin not yet holding the caller's grant,
        and a caller that is not confined to a namespace-local role's namespace
        (validateLocalRoleAssignment, handlers_authz.go:147-158). Only the first is retryable.

        userType is mandatory on a namespace-enabled cluster (handlers_authz.go:1620-1627).
        """
        return await self.post(
            f"/v1/authz/users/{user_id}/assign",
            json={"roles": roles, "userType": "db"},
            expect=(200, 403),
        )

    async def roles_for_user(self, user_id: str) -> list[str]:
        # The untyped /roles variant is 410 Gone on a namespaced cluster
        # (handlers_authz.go:946-949); the typed one is the only form valid on both clusters.
        payload = (await self.get(f"/v1/authz/users/{user_id}/roles/db")).json()
        return [role["name"] for role in payload or []]

    # --- backup and restore -------------------------------------------------

    async def backup_create(self, backend: str, body: dict[str, Any]) -> dict[str, Any]:
        """200, not 201. The response carries the resolved wildcard selection in `classes`."""
        return (await self.post(f"/v1/backups/{backend}", json=body, expect=(200,))).json()

    async def backup_status(self, backend: str, backup_id: str) -> dict[str, Any]:
        return (await self.get(f"/v1/backups/{backend}/{backup_id}")).json()

    async def backup_restore(
        self, backend: str, backup_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self.post(
            f"/v1/backups/{backend}/{backup_id}/restore", json=body, expect=(200,)
        )
        return response.json()

    async def restore_status(self, backend: str, backup_id: str) -> dict[str, Any]:
        return (await self.get(f"/v1/backups/{backend}/{backup_id}/restore")).json()

    async def list_backups(self, backend: str, read_timeout_s: float) -> httpx.Response:
        """Enumerates the backend, so a dead or misconfigured store surfaces here as 500."""
        return await self.get(
            f"/v1/backups/{backend}", expect=(200, 500), read_timeout_s=read_timeout_s
        )

    # --- replication --------------------------------------------------------

    async def scale_plan(self, class_name: str, replication_factor: int) -> httpx.Response:
        """Any status is returned; only the caller can say which ones are legal for its call.

        501 is the disabled stub. The live handler answers with whatever the leader query produced,
        including 500 for a class it cannot find: `failed to execute query: rpc error: code =
        NotFound desc = could not get replication scale plan: class not found: <Class>`. The
        preflight probe treats that as proof the live handler answered; graduate.py requires 200.
        """
        return await self.get(
            "/v1/replication/scale",
            params={"collection": class_name, "replicationFactor": replication_factor},
            expect=ANY_STATUS,
        )

    async def scale_apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        return (await self.post("/v1/replication/scale", json=plan, expect=(200,))).json()

    async def replication_details(self, operation_id: str) -> dict[str, Any]:
        return (await self.get(f"/v1/replication/replicate/{operation_id}")).json()

    async def replication_ops_for(self, class_name: str) -> list[dict[str, Any]]:
        response = await self.get(
            "/v1/replication/replicate/list", params={"collection": class_name}
        )
        return response.json() or []

    async def sharding_state(self, class_name: str) -> dict[str, Any]:
        response = await self.get(
            "/v1/replication/sharding-state", params={"collection": class_name}
        )
        return response.json().get("shardingState") or {}

    # --- objects ------------------------------------------------------------

    async def object_on_node(self, class_name: str, uuid: str, node: str) -> httpx.Response:
        """The only read that targets one replica, and it names that replica with node_name rather
        than by addressing its base URL.

        node_name and consistency_level are mutually exclusive (handlers_objects.go:985-987), so no
        consistency level is ever set here. node_name is honoured only when the shard has more than
        one read replica (adapters/repos/db/index.go:2031-2040); call this only after sharding state
        confirms rf=3.
        """
        return await self.get(
            f"/v1/objects/{class_name}/{uuid}",
            params={"include": "vector", "node_name": node},
            expect=(200, 404),
        )


def dynamic_user_ids(users: list[dict[str, Any]]) -> list[str]:
    """Dynamic users are exactly the db_user entries (entities/models/user_type_output.go:44-48)."""
    return [u["userId"] for u in users if u.get("dbUserType") == "db_user"]


def static_user_ids(users: list[dict[str, Any]]) -> list[str]:
    """The cluster's configured static API-key users, appended to every root-caller response."""
    return [u["userId"] for u in users if u.get("dbUserType") == "db_env_user"]


async def ready(base_url: str, *, connect_timeout_s: float, read_timeout_s: float) -> str | None:
    """Unauthenticated readiness probe. Returns None when ready, else a reason."""
    timeout = httpx.Timeout(
        connect=connect_timeout_s,
        write=connect_timeout_s,
        pool=connect_timeout_s,
        read=read_timeout_s,
    )
    async with httpx.AsyncClient(trust_env=False) as client:
        try:
            response = await client.get(f"{base_url}/v1/.well-known/ready", timeout=timeout)
        except httpx.HTTPError as exc:
            return repr(exc)
    if response.status_code != 200:
        return f"status {response.status_code}"
    return None
