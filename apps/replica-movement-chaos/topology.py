"""Isolated doc-sourced cluster/replication surface (``client.cluster.*``) + raw-REST helpers.

Every ``client.cluster.*`` call lives here so that, if a method name or return shape from
the docs turns out wrong on a live run, the fix is confined to this one file.
Per-node object counts come from raw REST ``GET /v1/nodes?output=verbose`` (wire-stable),
NOT the typed sharding-state / nodes() return shape.
"""

from typing import Any

import requests
import weaviate
from loguru import logger
from weaviate.cluster.models import ReplicationType

TERMINAL_STATES = {"READY", "CANCELLED"}


def _state_name(state: Any) -> str:
    """Normalise a ReplicateOperationState (enum member or raw string) to its upper name."""
    name = getattr(state, "name", None)
    if name is not None:
        return str(name).upper()
    return str(state).upper()


def is_terminal(state: Any) -> bool:
    """Only READY / CANCELLED are terminal; every other state (incl. enum-omitted INTEGRATING)
    is treated as in-progress so we never free a slot on a move that is still running."""
    return _state_name(state) in TERMINAL_STATES


async def replicate_move(
    coord: weaviate.WeaviateAsyncClient,
    collection: str,
    shard: str,
    source_node: str,
    target_node: str,
) -> str:
    op_id = await coord.cluster.replicate(
        collection=collection,
        shard=shard,
        source_node=source_node,
        target_node=target_node,
        replication_type=ReplicationType.MOVE,
    )
    return str(op_id)


async def sharding_state(
    coord: weaviate.WeaviateAsyncClient, collection: str
) -> dict[str, list[str]]:
    """Return {shard_name: [replica_node, ...]} from the typed cluster API."""
    state = await coord.cluster.query_sharding_state(collection=collection)
    return _parse_sharding_state(state)


def _parse_sharding_state(state: Any) -> dict[str, list[str]]:
    # Documented shape: state.shards -> list[ShardReplicas(name, replicas)]. Kept tolerant of a
    # dict-of-lists fallback; a truly unexpected shape is logged loudly (doc-sourced).
    result: dict[str, list[str]] = {}
    shards = getattr(state, "shards", None)
    if shards is None and isinstance(state, dict):
        shards = state.get("shards")
    if shards is None:
        logger.error("Unexpected sharding-state shape: {state!r}", state=state)
        return result
    for entry in shards:
        name = getattr(entry, "name", None) or getattr(entry, "shard", None)
        replicas = getattr(entry, "replicas", None)
        if name is None and isinstance(entry, dict):
            name = entry.get("name") or entry.get("shard")
            replicas = entry.get("replicas")
        if name is None or replicas is None:
            logger.error("Unexpected shard entry shape: {entry!r}", entry=entry)
            continue
        result[str(name)] = [str(r) for r in replicas]
    return result


def target_for(shard_replicas: list[str], all_nodes: list[str]) -> str | None:
    """The single node NOT currently holding the shard (rf=2, 3 nodes) — a legal MOVE target
    that dodges the pre-apply 422 'target replica already exists'. None if fully replicated."""
    candidates = [n for n in all_nodes if n not in shard_replicas]
    if not candidates:
        return None
    return candidates[0]


async def get_replication_op(coord: weaviate.WeaviateAsyncClient, op_id: str) -> Any:
    return await coord.cluster.replications.get(uuid=op_id, include_history=True)


def op_state(op: Any) -> str:
    return _state_name(getattr(getattr(op, "status", None), "state", "UNKNOWN"))


def op_errors(op: Any) -> list[Any]:
    errors = getattr(getattr(op, "status", None), "errors", None)
    return list(errors) if errors else []


def op_source(op: Any) -> str | None:
    src = getattr(op, "source_node", None)
    return str(src) if src is not None else None


def op_target(op: Any) -> str | None:
    tgt = getattr(op, "target_node", None)
    return str(tgt) if tgt is not None else None


async def cancel_replication(coord: weaviate.WeaviateAsyncClient, op_id: str) -> None:
    await coord.cluster.replications.cancel(uuid=op_id)


# --- raw REST (wire-stable; used for cross-checks and per-node object counts) ---


def fetch_nodes_verbose(http_base: str) -> dict[str, Any]:
    resp = requests.get(f"{http_base}/v1/nodes?output=verbose", timeout=30)
    resp.raise_for_status()
    return resp.json()


def node_names(http_base: str) -> list[str]:
    data = requests.get(f"{http_base}/v1/nodes", timeout=30)
    data.raise_for_status()
    return [n["name"] for n in data.json().get("nodes", [])]


def node_shard_object_count(nodes_json: dict[str, Any], node_name: str, shard: str) -> int | None:
    """objectCount for `shard` physically held on `node_name`, or None if that node has no such shard."""
    for node in nodes_json.get("nodes", []):
        if node.get("name") != node_name:
            continue
        for sh in node.get("shards", []) or []:
            if sh.get("name") == shard:
                return int(sh.get("objectCount", 0))
        return None
    return None
