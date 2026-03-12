import os
import time
import random

import numpy as np
import weaviate
from loguru import logger


host = os.getenv("HOST", "localhost")
host_port = int(os.getenv("HOST_PORT") or 8080)
host_grpc = os.getenv("HOST_GRPC", "localhost")
grpc_port = int(os.getenv("GRPC_PORT") or 50051)

class_name = os.getenv("CLASS_NAME", "MultiTenantClass")
tenant_name = os.getenv("TENANT_NAME", "Tenant1")

total_objects = int(os.getenv("TOTAL_OBJECTS") or 500000)
batch_size = int(os.getenv("BATCH_SIZE") or 1000)


def connect() -> weaviate.WeaviateClient:
    return weaviate.connect_to_custom(
        http_host=host,
        http_port=host_port,
        http_secure=False,
        grpc_host=host_grpc,
        grpc_port=grpc_port,
        grpc_secure=False,
    )


def import_objects(client: weaviate.WeaviateClient) -> None:
    logger.info(
        "Starting offload S3 importer: class={}, tenant={}, total_objects={}, batch_size={}",
        class_name,
        tenant_name,
        total_objects,
        batch_size,
    )

    created = 0
    start_all = time.time()

    while created < total_objects:
        current_batch = min(batch_size, total_objects - created)
        before = time.time()

        with client.batch.fixed_size(current_batch, 4) as batch:
            for i in range(current_batch):
                idx = created + i
                props = {
                    "name": f"extra-{idx}",
                }
                # Use a small fixed vector size; actual size is irrelevant for this chaos test
                vector = np.random.rand(1, 16)[0].tolist()
                batch.add_object(
                    class_name,
                    props,
                    tenant=tenant_name,
                    vector=vector,
                )

        created += current_batch
        took = time.time() - before
        logger.info(
            "Imported batch: {} objects (total {}/{}), took {:.2f}s, elapsed {:.2f}s",
            current_batch,
            created,
            total_objects,
            took,
            time.time() - start_all,
        )

    logger.success(
        "Finished importing {} objects into class={} tenant={} in {:.2f}s",
        total_objects,
        class_name,
        tenant_name,
        time.time() - start_all,
    )


if __name__ == "__main__":
    client = connect()
    import_objects(client)
