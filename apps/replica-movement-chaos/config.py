import os
from dataclasses import dataclass, field

from loguru import logger


@dataclass(frozen=True)
class NodeSpec:
    """A single Weaviate pod addressed by its forwarded HTTP + gRPC ports."""

    name: str
    http_host: str
    http_port: int
    grpc_port: int


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(f"Invalid int for {name}={value!r}; using default {default}")
        return default


def get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning(f"Invalid float for {name}={value!r}; using default {default}")
        return default


def get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def parse_nodes(raw: str) -> list[NodeSpec]:
    """Parse WEAVIATE_NODES = 'name=http_host:http_port:grpc_port,...' (order preserved; [0]=coordinator)."""
    specs: list[NodeSpec] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            name, addr = entry.split("=", 1)
            http_host, http_port, grpc_port = addr.split(":")
            specs.append(
                NodeSpec(
                    name=name.strip(),
                    http_host=http_host.strip(),
                    http_port=int(http_port),
                    grpc_port=int(grpc_port),
                )
            )
        except ValueError as e:
            raise ValueError(
                f"Invalid WEAVIATE_NODES entry {entry!r}; expected 'name=http_host:http_port:grpc_port'"
            ) from e
    if not specs:
        raise ValueError("WEAVIATE_NODES is required and must list at least one node")
    return specs


@dataclass(frozen=True)
class Config:
    nodes: list[NodeSpec]
    collection: str
    tenant_count: int
    objects_per_tenant: int
    rf: int
    duration: int
    backup_enabled: bool
    backup_backend: str
    backup_delay_min: int
    backup_delay_max: int
    backup_restore_enabled: bool
    backup_restore_min_fraction: float
    restore_timeout: int
    restore_verify_timeout: int
    mutate_concurrency: int
    mutate_interval_ms: int
    tenant_interval_ms: int
    tenant_conflict_inject_rate: float
    move_interval_ms: int
    move_max_inflight: int
    move_conflict_inject_rate: float
    move_poll_interval: float
    preflight_move_timeout: int
    retry_budget: int
    retry_backoff_ms: int
    settle_timeout: int
    drain_timeout: int
    node_local_sample: int
    node_local_concurrency: int
    node_local_converge_timeout: int
    verify_timeout: int
    verify_concurrency: int
    seed: int

    tenant_names: list[str] = field(default_factory=list)

    @staticmethod
    def from_env() -> "Config":
        nodes_raw = os.getenv("WEAVIATE_NODES")
        if not nodes_raw:
            raise ValueError("WEAVIATE_NODES environment variable is required")
        nodes = parse_nodes(nodes_raw)

        tenant_count = get_env_int("TENANT_COUNT", 100)
        # Pulled out so the verify-convergence/parallelism knobs can default off them.
        settle_timeout = get_env_int("SETTLE_TIMEOUT", 120)
        node_local_concurrency = get_env_int("NODE_LOCAL_CONCURRENCY", 32)
        # Pulled out so restore verification defaults ON exactly when backup is enabled.
        backup_enabled = get_env_bool("BACKUP_ENABLED", True)
        cfg = Config(
            nodes=nodes,
            collection=os.getenv("COLLECTION", "ReplicaMovementChaos"),
            tenant_count=tenant_count,
            objects_per_tenant=get_env_int("OBJECTS_PER_TENANT", 1000),
            rf=get_env_int("REPLICATION_FACTOR", 2),
            duration=get_env_int("DURATION", 180),
            backup_enabled=backup_enabled,
            backup_backend=os.getenv("BACKUP_BACKEND", "s3"),
            backup_delay_min=get_env_int("BACKUP_DELAY_MIN", 20),
            backup_delay_max=get_env_int("BACKUP_DELAY_MAX", 120),
            # C8/F8: after the live-data checks, restore the backup and assert no wholesale shard loss
            # vs a backup-start snapshot. Defaults ON with backup so a SUCCESS-but-corrupt backup (a
            # MOVE deleting a source shard mid-backup) cannot pass green on create-status alone.
            backup_restore_enabled=get_env_bool("BACKUP_RESTORE_ENABLED", backup_enabled),
            # Lower-bound tolerance: a restored tenant below this fraction of its snapshot count is a
            # lost-shard FINDING. Deliberately loose (writes/deletes race the backup) so it stays a
            # no-lost-shard property, NOT flaky object-equality.
            backup_restore_min_fraction=get_env_float("BACKUP_RESTORE_MIN_FRACTION", 0.5),
            restore_timeout=get_env_int("RESTORE_TIMEOUT", 300),
            # Ceiling on the post-restore count-read loop, which runs OUTSIDE verify_timeout (restore
            # must proceed even after a content-phase timeout) and so has no other overall bound; on
            # exceed restore-verify FINDINGs rather than hanging on a degraded cluster. The restore
            # itself is separately bounded by RESTORE_TIMEOUT.
            restore_verify_timeout=get_env_int("RESTORE_VERIFY_TIMEOUT", 300),
            mutate_concurrency=get_env_int("MUTATE_CONCURRENCY", 8),
            mutate_interval_ms=get_env_int("MUTATE_INTERVAL_MS", 0),
            tenant_interval_ms=get_env_int("TENANT_INTERVAL_MS", 500),
            tenant_conflict_inject_rate=get_env_float("TENANT_CONFLICT_INJECT_RATE", 0.2),
            move_interval_ms=get_env_int("MOVE_INTERVAL_MS", 1000),
            move_max_inflight=get_env_int("MOVE_MAX_INFLIGHT", 3),
            move_conflict_inject_rate=get_env_float("MOVE_CONFLICT_INJECT_RATE", 0.15),
            move_poll_interval=get_env_float("MOVE_POLL_INTERVAL", 2),
            preflight_move_timeout=get_env_int("PREFLIGHT_MOVE_TIMEOUT", 120),
            retry_budget=get_env_int("RETRY_BUDGET", 30),
            retry_backoff_ms=get_env_int("RETRY_BACKOFF_MS", 200),
            settle_timeout=settle_timeout,
            drain_timeout=get_env_int("DRAIN_TIMEOUT", 180),
            # NODE_LOCAL_SAMPLE default 0 = FULL tenant coverage for the authoritative per-node
            # content check (a sampling gap would hide divergence in unchecked tenants); a positive
            # value samples that many tenants instead, as a speed knob.
            node_local_sample=get_env_int("NODE_LOCAL_SAMPLE", 0),
            node_local_concurrency=node_local_concurrency,
            # Post-chaos a replica can still be reconciling once counts match; verify re-polls a
            # mismatch for this long, clearing it ONLY on a fresh authoritative match, before it
            # becomes a FINDING. Bounded so a permanent divergence is never waited out into a pass.
            node_local_converge_timeout=get_env_int("NODE_LOCAL_CONVERGE_TIMEOUT", settle_timeout),
            # Hard ceiling on the whole verify (settle -> node-local); on exceed verify FINDINGs and
            # stops rather than hanging. Generous by default (a full ~200k-read pass is minutes, not
            # this); it is a backstop against a degraded cluster, tune down if desired.
            verify_timeout=get_env_int("VERIFY_TIMEOUT", 1800),
            # Bounds the per-tenant fan-out in settle/read-back so at most this many tenants' object
            # sets are held in memory (and connections in flight) at once.
            verify_concurrency=get_env_int("VERIFY_CONCURRENCY", node_local_concurrency),
            seed=get_env_int("SEED", 0),
            tenant_names=[f"tenant-{i}" for i in range(tenant_count)],
        )
        return cfg
