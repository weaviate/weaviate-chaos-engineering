import random
from loguru import logger
from typing import Optional
import uuid
import weaviate
import weaviate.classes as wvc
import h5py
import time

class_name = "Vector"


def reset_schema(client: weaviate.WeaviateClient, efC, m, shards, distance):
    client.collections.delete_all()
    client.collections.create(
        name=class_name,
        vectorizer_config=wvc.Configure.Vectorizer.none(),
        vector_index_config=wvc.Configure.vector_index(
            ef_construction=efC,
            max_connections=m,
            ef=-1,
            distance_metric=wvc.VectorDistance(distance),
        ),
        properties=[
            wvc.Property(
                name="i",
                data_type=wvc.DataType.INT,
            )
        ],
        inverted_index_config=wvc.Configure.inverted_index(index_timestamps=False),
        sharding_config=wvc.Configure.sharding(desired_count=shards),
    )


def load_records(client: weaviate.WeaviateClient, vectors, compression, dim_to_seg_ratio, override):
    collection = client.collections.get(class_name)
    i = 0
    if vectors == None:
        vectors = [None] * 10_000_000
    client.batch.configure(dynamic=False, batch_size=1000)
    with client.batch as batch:
        for vector in vectors:
            if i % 10000 == 0:
                logger.info(f"writing record {i}/{len(vectors)}")

            if i == 100000 and compression == True and override == False:
                logger.info(f"pausing import to enable compression")
                break

            data_object = {
                "i": i,
            }

            batch.add_object(
                properties=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
            i += 1

    for err in batch.failed_objects():
        logger.error(err.message)

    if compression == True and override == False:
        collection.config.update(
            wvc.Reconfigure.vector_index(
                pq_enabled=True,
                pq_segments=int(len(vectors[0]) / dim_to_seg_ratio),
            )
        )

        wait_for_all_shards_ready(client)

        i = 100000
        with client.batch as batch:
            while i < len(vectors):
                vector = vectors[i]
                if i % 10000 == 0:
                    logger.info(f"writing record {i}/{len(vectors)}")

                data_object = {
                    "i": i,
                }

                batch.add_object(
                    properties=data_object,
                    vector=vector,
                    class_name=class_name,
                    uuid=uuid.UUID(int=i),
                )
                i += 1
    logger.info(f"Finished writing {len(vectors)} records")


def wait_for_all_shards_ready(client: weaviate.Client):
    status = [s["status"] for s in client.schema.get_class_shards(class_name)]
    if not all(s == "READONLY" for s in status):
        raise Exception(f"shards are not READONLY at beginning: {status}")

    max_wait = 300
    before = time.time()

    while True:
        time.sleep(3)
        status = [s["status"] for s in client.schema.get_class_shards(class_name)]
        if all(s == "READY" for s in status):
            logger.info(f"finished in {time.time()-before}s")
            return

        if time.time() - before > max_wait:
            raise Exception(f"after {max_wait}s not all shards READY: {status}")
