"""Every environment tunable of the namespace-graduation journey, and the names it derives from them.

No other module reads os.environ.
"""

import json
import os
import random
import re
import string
from dataclasses import dataclass, field
from typing import Any

# entities/schema/validation.go:131-149
NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,34}[a-z0-9]$")
CLASS_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
BACKUP_ID_RE = re.compile(r"^[a-z0-9_-]+$")

BASE36 = string.digits + string.ascii_lowercase


class ConfigError(Exception):
    """Raised for any invalid or missing configuration. No network call happens before this is clear."""


def _raw(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return ""
    return value.strip()


def _env_str(name: str, default: str | None = None) -> str:
    value = _raw(name)
    if value == "":
        if default is None:
            raise ConfigError(f"{name} is required")
        return default
    return value


def _env_int(name: str, default: int) -> int:
    value = _raw(name)
    if value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc


def _env_float(name: str, default: float) -> float:
    value = _raw(name)
    if value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = _raw(name).lower()
    if value == "":
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean, got {value!r}")


def _env_json(name: str) -> dict[str, str] | None:
    value = _raw(name)
    if value == "":
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{name} must be valid JSON, got {value!r}") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
    ):
        raise ConfigError(f"{name} must be a JSON object of string to string, got {value!r}")
    return parsed


def _env_int_list(name: str, default: str) -> list[int]:
    value = _env_str(name, default)
    try:
        return [int(part.strip()) for part in value.split(",") if part.strip() != ""]
    except ValueError as exc:
        raise ConfigError(f"{name} must be a comma-separated list of ports, got {value!r}") from exc


def _env_str_list(name: str) -> list[str]:
    value = _raw(name)
    if value == "":
        return []
    return [part.strip() for part in value.split(",") if part.strip() != ""]


def generate_run_id() -> str:
    return "".join(random.choice(BASE36) for _ in range(6))


@dataclass(frozen=True)
class Cluster:
    """One Weaviate cluster's addressing and its static root credential."""

    label: str
    host: str
    http_ports: list[int]
    grpc_ports: list[int]
    http_base_urls: list[str]
    root_api_key: str

    def grpc_port(self, index: int) -> int:
        return self.grpc_ports[index % len(self.grpc_ports)]

    def http_port(self, index: int) -> int:
        return self.http_ports[index % len(self.http_ports)]


def _cluster(label: str, prefix: str, host: str, default_http: str, default_grpc: str) -> Cluster:
    if _raw(f"{prefix}_HTTP_BASE_URLS") != "":
        # A base URL sets REST addressing only. The weaviate client needs a gRPC host and port, and
        # neither is derivable from an HTTP base URL, so honouring the override would silently split
        # REST and gRPC across different endpoints.
        raise ConfigError(
            f"{prefix}_HTTP_BASE_URLS is refused: SOURCE_HTTP_BASE_URLS and "
            "TARGET_HTTP_BASE_URLS override REST addressing only, and no gRPC host or port can be "
            f"derived from them. Address a non-default rig with WEAVIATE_HOST_ADDR plus "
            f"{prefix}_HTTP_PORTS and {prefix}_GRPC_PORTS."
        )
    http_ports = _env_int_list(f"{prefix}_HTTP_PORTS", default_http)
    grpc_ports = _env_int_list(f"{prefix}_GRPC_PORTS", default_grpc)
    if len(http_ports) != len(grpc_ports):
        raise ConfigError(
            f"{prefix}_HTTP_PORTS and {prefix}_GRPC_PORTS must have equal length, "
            f"got {len(http_ports)} and {len(grpc_ports)}"
        )
    return Cluster(
        label=label,
        host=host,
        http_ports=http_ports,
        grpc_ports=grpc_ports,
        http_base_urls=[f"http://{host}:{port}" for port in http_ports],
        root_api_key=_env_str(f"{prefix}_ROOT_API_KEY"),
    )


@dataclass(frozen=True)
class Config:
    weaviate_version: str
    run_id: str
    source: Cluster
    target: Cluster

    namespace_prefix: str
    neighbour_namespace_count: int
    collection_prefix: str
    collections_per_namespace: int
    objects_per_collection: int
    vector_dim: int
    users_per_namespace: int
    neighbour_set_target: int

    backup_backend: str
    backup_id_prefix: str
    target_replication_factor: int
    allow_target_store_replacement: bool

    poll_interval_s: float
    rest_connect_timeout_s: float
    rest_read_timeout_s: float
    raft_visibility_timeout_s: float
    backup_timeout_s: float
    restore_timeout_s: float
    scale_op_timeout_s: float
    sharding_state_timeout_s: float
    namespace_delete_timeout_s: float
    per_replica_sweep_timeout_s: float
    per_replica_sweep_concurrency: int
    count_converge_floor_s: float
    load_pause_timeout_s: float
    neighbour_load_ops_per_second: float

    restore_node_mapping: dict[str, str] | None
    restore_include: list[str] = field(default_factory=list)

    # --- derived names -------------------------------------------------------

    @property
    def namespace_count(self) -> int:
        """Index 1 graduates; 2..N are neighbours."""
        return self.neighbour_namespace_count + 1

    def namespace_name(self, index: int) -> str:
        return f"{self.namespace_prefix}-{self.run_id}-ns{index}"

    @property
    def graduating_namespace(self) -> str:
        return self.namespace_name(1)

    def neighbour_namespaces(self) -> list[str]:
        return [self.namespace_name(i) for i in range(2, self.namespace_count + 1)]

    def collection_short_name(self, serial: int) -> str:
        """serial counts across the whole run, so no two namespaces produce the same stripped class name."""
        return f"{self.collection_prefix}{self.run_id.capitalize()}Coll{serial}"

    def collection_serials(self, namespace_index: int) -> list[int]:
        first = (namespace_index - 1) * self.collections_per_namespace + 1
        return list(range(first, first + self.collections_per_namespace))

    def user_short_name(self, serial: int) -> str:
        """serial counts across the whole run, for the same reason as collection_short_name."""
        return f"u{self.run_id}{serial}"

    def user_serials(self, namespace_index: int) -> list[int]:
        first = (namespace_index - 1) * self.users_per_namespace + 1
        return list(range(first, first + self.users_per_namespace))

    def role_short_name(self, namespace_index: int) -> str:
        return f"w{self.run_id}{namespace_index}"

    @property
    def backup_id(self) -> str:
        return f"{self.backup_id_prefix}_{self.run_id}"

    def all_collection_short_names(self) -> list[str]:
        return [
            self.collection_short_name(serial)
            for index in range(1, self.namespace_count + 1)
            for serial in self.collection_serials(index)
        ]

    def graduating_collection_short_names(self) -> list[str]:
        return [self.collection_short_name(serial) for serial in self.collection_serials(1)]

    def graduating_user_short_names(self) -> list[str]:
        """The user ids this run's restore will produce on the target, after the namespace strip."""
        return [self.user_short_name(serial) for serial in self.user_serials(1)]

    def probe_class_name(self, user_short_name: str) -> str:
        """The throwaway class one migrated user's capability probe creates on the target."""
        return f"{self.graduating_collection_short_names()[0]}Probe{user_short_name.capitalize()}"

    def probe_class_names(self) -> list[str]:
        """Derivable before anything exists, so the artefact summary and preflight both name them.

        Every migrated user's probe is listed, not only the admin's: a narrow user's probe leaves a
        class behind exactly when its denial fails.
        """
        return [self.probe_class_name(name) for name in self.graduating_user_short_names()]

    # --- validation ----------------------------------------------------------

    def validate(self) -> None:
        if self.neighbour_namespace_count < 2:
            raise ConfigError("NEIGHBOUR_NAMESPACE_COUNT must be at least 2")
        if self.collections_per_namespace < 2:
            raise ConfigError("COLLECTIONS_PER_NAMESPACE must be at least 2")
        if self.users_per_namespace < 2:
            raise ConfigError("USERS_PER_NAMESPACE must be at least 2")
        if self.objects_per_collection < 1:
            raise ConfigError("OBJECTS_PER_COLLECTION must be at least 1")
        if self.per_replica_sweep_concurrency < 1:
            raise ConfigError("PER_REPLICA_SWEEP_CONCURRENCY must be at least 1")
        if self.neighbour_load_ops_per_second <= 0:
            raise ConfigError("NEIGHBOUR_LOAD_OPS_PER_SECOND must be positive")
        if not BACKUP_ID_RE.match(self.backup_id):
            raise ConfigError(
                f"derived backup id {self.backup_id!r} must match {BACKUP_ID_RE.pattern} "
                "— check BACKUP_ID_PREFIX and RUN_ID"
            )
        for index in range(1, self.namespace_count + 1):
            name = self.namespace_name(index)
            if not NAMESPACE_RE.match(name):
                raise ConfigError(
                    f"derived namespace {name!r} must match {NAMESPACE_RE.pattern} "
                    "— check NAMESPACE_PREFIX and RUN_ID"
                )
        for name in self.all_collection_short_names() + self.probe_class_names():
            if not CLASS_RE.match(name):
                raise ConfigError(
                    f"derived collection {name!r} must match {CLASS_RE.pattern} "
                    "— check COLLECTION_PREFIX and RUN_ID"
                )

    @staticmethod
    def from_env() -> "Config":
        host = _env_str("WEAVIATE_HOST_ADDR", "host.docker.internal")
        cfg = Config(
            weaviate_version=_env_str("WEAVIATE_VERSION"),
            run_id=_env_str("RUN_ID", generate_run_id()).lower(),
            # The source cluster is a single node (docker-compose-namespaces-source.yml).
            source=_cluster("source", "SOURCE", host, "18080", "18090"),
            target=_cluster("target", "TARGET", host, "18180,18181,18182", "18190,18191,18192"),
            namespace_prefix=_env_str("NAMESPACE_PREFIX", "grad"),
            neighbour_namespace_count=_env_int("NEIGHBOUR_NAMESPACE_COUNT", 2),
            collection_prefix=_env_str("COLLECTION_PREFIX", "Grad"),
            collections_per_namespace=_env_int("COLLECTIONS_PER_NAMESPACE", 2),
            objects_per_collection=_env_int("OBJECTS_PER_COLLECTION", 200),
            vector_dim=_env_int("VECTOR_DIM", 32),
            users_per_namespace=_env_int("USERS_PER_NAMESPACE", 2),
            neighbour_set_target=_env_int("NEIGHBOUR_SET_TARGET", 400),
            backup_backend=_env_str("BACKUP_BACKEND", "s3"),
            backup_id_prefix=_env_str("BACKUP_ID_PREFIX", "nsgrad"),
            target_replication_factor=_env_int("TARGET_REPLICATION_FACTOR", 3),
            allow_target_store_replacement=_env_bool("ALLOW_TARGET_STORE_REPLACEMENT", False),
            poll_interval_s=_env_float("POLL_INTERVAL_S", 3),
            rest_connect_timeout_s=_env_float("REST_CONNECT_TIMEOUT_S", 5),
            rest_read_timeout_s=_env_float("REST_READ_TIMEOUT_S", 30),
            raft_visibility_timeout_s=_env_float("RAFT_VISIBILITY_TIMEOUT_S", 30),
            backup_timeout_s=_env_float("BACKUP_TIMEOUT_S", 600),
            restore_timeout_s=_env_float("RESTORE_TIMEOUT_S", 900),
            scale_op_timeout_s=_env_float("SCALE_OP_TIMEOUT_S", 600),
            sharding_state_timeout_s=_env_float("SHARDING_STATE_TIMEOUT_S", 120),
            namespace_delete_timeout_s=_env_float("NAMESPACE_DELETE_TIMEOUT_S", 300),
            per_replica_sweep_timeout_s=_env_float("PER_REPLICA_SWEEP_TIMEOUT_S", 300),
            per_replica_sweep_concurrency=_env_int("PER_REPLICA_SWEEP_CONCURRENCY", 8),
            count_converge_floor_s=_env_float("COUNT_CONVERGE_FLOOR_S", 10),
            load_pause_timeout_s=_env_float("LOAD_PAUSE_TIMEOUT_S", 60),
            neighbour_load_ops_per_second=_env_float("NEIGHBOUR_LOAD_OPS_PER_SECOND", 10),
            restore_node_mapping=_env_json("RESTORE_NODE_MAPPING"),
            restore_include=_env_str_list("RESTORE_INCLUDE"),
        )
        cfg.validate()
        return cfg

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "weaviate_version": self.weaviate_version,
            "source_urls": self.source.http_base_urls,
            "target_urls": self.target.http_base_urls,
            "graduating_namespace": self.graduating_namespace,
            "neighbour_namespaces": self.neighbour_namespaces(),
            "collections": self.all_collection_short_names(),
            "backup_id": self.backup_id,
        }
