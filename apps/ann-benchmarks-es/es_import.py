import random
from loguru import logger
from typing import Optional
import uuid
from elasticsearch import ConnectionError, Elasticsearch
from elasticsearch.helpers import bulk
import h5py
import time

def reset_schema(client, index_name, efC, m, shards, distance, dimensions):
    settings = {
        "number_of_shards": shards,
        "number_of_replicas": 0,
        "refresh_interval": -1,
    }
    mappings = {
        "properties": {
            "id": {"type": "keyword", "store": True},
            "vec": {
                "type": "dense_vector",
                "element_type": "float",
                "dims": dimensions,
                "index": True,
                "similarity": distance,
                "index_options": {
                    "type": "hnsw",
                    "m": m,
                    "ef_construction": efC,
                },
            },
        },
    }
    client.indices.delete(index=index_name)
    client.indices.create(index=index_name, settings=settings, mappings=mappings)
    wait_for_health_status(client)

    return index_name

def load_records(client, index_name, vectors):
    def gen():
        for i, vec in enumerate(vectors):
            yield {"_op_type": "index", "_index": index_name, "id": str(i), "vec": vec}

    print("Indexing ...")
    (_, errors) = bulk(client, gen(), chunk_size=100, request_timeout=90)
    if len(errors) != 0:
        raise RuntimeError("Failed to index documents")

    # Optimzes segment count and size
    print("Force merge index ...")
    client.indices.forcemerge(index=index_name, max_num_segments=1, request_timeout=900)

    # Makes data visible as we set (automatic) refresh_interval to -1 when importing, for speed
    print("Refreshing index ...")
    client.indices.refresh(index=index_name, request_timeout=900)

# yellow = all primary shards ready; green = all primaries and replicas
#
# running in single node/shard yellow and green are equivalent since
# any replicas won't be able to be placed
def wait_for_health_status(client, wait_seconds=30, status="yellow"):
    print("Waiting for Elasticsearch ...")
    for _ in range(wait_seconds):
        try:
            health = client.cluster.health(wait_for_status=status, request_timeout=1)
            print(f'Elasticsearch is ready: status={health["status"]}')
            return
        except ConnectionError:
            pass
        sleep(1)
    raise RuntimeError("Failed to connect to Elasticsearch")