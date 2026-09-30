import asyncio
import random

import weaviate
from loguru import logger

from config import Config, NodeSpec


class Clients:
    """Holds one node-pinned async client per pod.

    coordinator (clients[0]) owns schema / cluster / backup / CL.ALL operations;
    the per-pod clients back node-local reads and random-node write distribution.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self.clients: list[weaviate.WeaviateAsyncClient] = []
        self.by_node_name: dict[str, weaviate.WeaviateAsyncClient] = {}

    @property
    def coordinator(self) -> weaviate.WeaviateAsyncClient:
        return self.clients[0]

    def random_node_client(self) -> weaviate.WeaviateAsyncClient:
        return random.choice(self.clients)

    async def connect(self) -> None:
        for spec in self._cfg.nodes:
            client = _build_client(spec)
            await _connect_with_retry(client, spec)
            self.clients.append(client)
            self.by_node_name[spec.name] = client
            logger.info(
                "Connected node client {name} -> {host}:{hp}/{gp}",
                name=spec.name,
                host=spec.http_host,
                hp=spec.http_port,
                gp=spec.grpc_port,
            )

    async def close(self) -> None:
        for client in self.clients:
            try:
                await client.close()
            except Exception as e:  # best-effort teardown; never mask the primary failure
                logger.warning("Error closing client: {e}", e=e)


def _build_client(spec: NodeSpec) -> weaviate.WeaviateAsyncClient:
    # use_async_with_custom is the async sibling of connect_to_custom; both pairs must
    # point at this pod's forwarded ports so gRPC data-plane traffic reaches the same node.
    return weaviate.use_async_with_custom(
        http_host=spec.http_host,
        http_port=spec.http_port,
        http_secure=False,
        grpc_host=spec.http_host,
        grpc_port=spec.grpc_port,
        grpc_secure=False,
    )


async def _connect_with_retry(
    client: weaviate.WeaviateAsyncClient, spec: NodeSpec, attempts: int = 30
) -> None:
    """Bounded retry absorbs the gRPC-settle gap after the script's port-forwards come up."""
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            await client.connect()
            if await client.is_ready():
                return
            last_err = RuntimeError("is_ready() returned False")
        except Exception as e:
            last_err = e
        logger.warning(
            "Node {name} not ready (attempt {i}/{n}): {e}",
            name=spec.name,
            i=i + 1,
            n=attempts,
            e=last_err,
        )
        await asyncio.sleep(1)
    raise RuntimeError(
        f"Node {spec.name} did not become ready after {attempts} attempts: {last_err}"
    )
