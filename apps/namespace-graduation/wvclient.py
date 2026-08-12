"""weaviate-client v4.22.0 construction and the usage rules the pinned tag imposes."""

import random
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import weaviate
from weaviate.classes.init import Auth
from weaviate.client import WeaviateAsyncClient
from weaviate.exceptions import InsufficientPermissionsError, UnexpectedStatusCodeError

from config import Cluster
from model import ExpectedObject

# Configure.Vectors.self_provided() creates a named vector called "default"
# (client-ref/weaviate/collections/classes/config.py:2496-2501). On such a collection the server
# rejects a legacy top-level "vector", so every write sends {VECTOR_NAME: [...]} and every read asks
# for include_vector=True — a bare name string silently returns no vector (classes/grpc.py:147-160).
VECTOR_NAME = "default"

# usecases/usagelimits/errors.go:31-43
USAGE_LIMIT_ERROR_CODE = "USAGE_LIMIT_EXCEEDED"


def build_client(
    cluster: Cluster, api_key: str, *, index: int = 0, skip_init_checks: bool = True
) -> WeaviateAsyncClient:
    """Build (but do not connect) an async client against one node of a cluster.

    There is no async connect_to_custom at this tag; the factory only builds, and connect() is
    mandatory before any other call (client-ref/weaviate/connect/helpers.py:579-660).
    """
    return weaviate.use_async_with_custom(
        http_host=cluster.host,
        http_port=cluster.http_port(index),
        http_secure=False,
        grpc_host=cluster.host,
        grpc_port=cluster.grpc_port(index),
        grpc_secure=False,
        auth_credentials=Auth.api_key(api_key),
        skip_init_checks=skip_init_checks,
    )


@asynccontextmanager
async def connected(
    cluster: Cluster, api_key: str, *, index: int = 0, skip_init_checks: bool = True
) -> AsyncIterator[WeaviateAsyncClient]:
    client = build_client(cluster, api_key, index=index, skip_init_checks=skip_init_checks)
    await client.connect()
    try:
        yield client
    finally:
        await client.close()


def is_permission_denied(exc: BaseException) -> bool:
    """403 on REST paths, gRPC code 7 on gRPC paths. Both raise InsufficientPermissionsError."""
    return isinstance(exc, InsufficientPermissionsError) and exc.status_code in (403, 7)


def usage_limit_rejection(exc: BaseException) -> str | None:
    """The rig's MAXIMUM_ALLOWED_OBJECTS_COUNT was hit. Returns the server's payload, else None.

    Surfaces as HTTP 429 or gRPC RESOURCE_EXHAUSTED, both carrying errorCode USAGE_LIMIT_EXCEEDED.
    Matched on the machine-readable error code, never on prose.
    """
    text = str(exc)
    if USAGE_LIMIT_ERROR_CODE in text:
        return text
    if isinstance(exc, UnexpectedStatusCodeError) and exc.status_code == 429:
        return text
    return None


def usage_limit_in_batch_errors(errors: dict[Any, Any]) -> str | None:
    """insert_many reports per-object failures rather than raising; inspect them the same way."""
    for error in errors.values():
        message = getattr(error, "message", str(error))
        if USAGE_LIMIT_ERROR_CODE in message:
            return message
    return None


def random_vector(rng: random.Random, dim: int) -> list[float]:
    return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


async def read_all(collection: Any) -> dict[str, ExpectedObject]:
    """Read a whole collection through the client, vectors included, keyed by uuid."""
    out: dict[str, ExpectedObject] = {}
    async for obj in collection.iterator(include_vector=True):
        out[str(obj.uuid)] = ExpectedObject(
            uuid=str(obj.uuid),
            properties=dict(obj.properties),
            vectors={name: list(values) for name, values in (obj.vector or {}).items()},
        )
    return out
