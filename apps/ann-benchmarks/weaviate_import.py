import os
import random
from loguru import logger
from typing import Optional
import uuid
import weaviate
import weaviate.classes.config as wvc
import h5py
import time

class_name = "Vector"


def reset_schema(client: weaviate.WeaviateClient, efC, m, shards, distance):
    client.collections.delete_all()
    client.collections.create(
        name=class_name,
        vectorizer_config=wvc.Configure.Vectorizer.none(),
        vector_index_config=wvc.Configure.VectorIndex.hnsw(
            ef_construction=efC,
            max_connections=m,
            ef=-1,
            distance_metric=wvc.VectorDistances(distance),
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


def load_records(
    client: weaviate.WeaviateClient, vectors, quantization, dim_to_seg_ratio, override
):
    collection = client.collections.get(class_name)
    i = 0
    if vectors == None:
        vectors = [None] * 10_000_000
    batch_size = 1000
    len_objects = len(vectors)

    with client.batch.fixed_size(batch_size=batch_size) as batch:
        for vector in vectors:
            if i == 100000 and quantization in ["pq", "sq", "lasq"] and override == False:
                logger.info(f"pausing import to enable quantization")
                break

            if i % 10000 == 0:
                logger.info(f"writing record {i}/{len_objects}")

            data_object = {
                "i": i,
            }
            batch.add_object(
                properties=data_object,
                vector=vector,
                collection=class_name,
                uuid=uuid.UUID(int=i),
            )
            i += 1

    for err in client.batch.failed_objects:
        logger.error(err.message)

    if quantization in ["pq", "sq", "lasq"] and override == False:

        if quantization == "pq":
            collection.config.update(
                vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                    quantizer=wvc.Reconfigure.VectorIndex.Quantizer.pq(
                        segments=int(len(vectors[0]) / dim_to_seg_ratio),
                    )
                )
            )
        elif quantization == "sq":
            collection.config.update(
                vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                    quantizer=wvc.Reconfigure.VectorIndex.Quantizer.sq()
                )
            )
        elif quantization == "lasq":
            collection.config.update(
                vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                    quantizer=wvc.Reconfigure.VectorIndex.Quantizer.lasq()
                )
            )

        wait_for_all_shards_ready(collection)

        i = 100000
        with client.batch.fixed_size(batch_size=batch_size) as batch:
            while i < len_objects:
                vector = vectors[i]
                if i % 10000 == 0:
                    logger.info(f"writing record {i}/{len_objects}")

                data_object = {
                    "i": i,
                }

                batch.add_object(
                    properties=data_object,
                    vector=vector,
                    collection=class_name,
                    uuid=uuid.UUID(int=i),
                )
                i += 1

        for err in client.batch.failed_objects:
            logger.error(err.message)

    logger.info(f"Finished writing {len_objects} records")


def wait_for_all_shards_ready(collection: weaviate.collections.Collection):
    status = [s.status for s in collection.config.get_shards()]
    if not all(s == "READONLY" for s in status):
        raise Exception(f"shards are not READONLY at beginning: {status}")

    max_wait = 300
    before = time.time()

    while True:
        time.sleep(3)
        status = [s.status for s in collection.config.get_shards()]
        if all(s == "READY" for s in status):
            logger.info(f"finished in {time.time()-before}s")
            return

        if time.time() - before > max_wait:
            raise Exception(f"after {max_wait}s not all shards READY: {status}")
