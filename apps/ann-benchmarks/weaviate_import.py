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


def reset_schema(client: weaviate.WeaviateClient, efC, m, shards, distance, multivector=False):
    client.collections.delete_all()
    client.collections.create(
        name=class_name,
        vectorizer_config=(
            wvc.Configure.Vectorizer.none()
            if not multivector
            else [
                wvc.Configure.NamedVectors.none(
                    name="multivector",
                    vector_index_config=wvc.Configure.VectorIndex.hnsw(
                        ef_construction=efC,
                        max_connections=m,
                        ef=-1,
                        multi_vector=wvc.Configure.VectorIndex.MultiVector.multi_vector(),
                    ),
                )
            ]
        ),
        vector_index_config=(
            wvc.Configure.VectorIndex.hnsw(
                ef_construction=efC,
                max_connections=m,
                ef=-1,
                distance_metric=wvc.VectorDistances(distance),
            )
            if not multivector
            else None
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
    client: weaviate.WeaviateClient,
    vectors,
    quantization,
    dim_to_seg_ratio,
    override,
    multivector=False,
):
    collection = client.collections.get(class_name)
    i = 0
    if vectors == None:
        vectors = [None] * 10_000_000
    batch_size = 100
    len_objects = len(vectors)

    with client.batch.fixed_size(batch_size=batch_size) as batch:
        for vector in vectors:
            if i == 100000 and quantization in ["pq", "sq"] and override == False:
                logger.info(f"pausing import to enable quantization")
                break

            if i % 10000 == 0:
                logger.info(f"writing record {i}/{len_objects}")

            data_object = {
                "i": i,
            }
            multivector_object = {}
            if multivector:
                multivector_object["multivector"] = vector
            batch.add_object(
                properties=data_object,
                vector=vector if multivector is False else multivector_object,
                uuid=uuid.UUID(int=i),
                collection=class_name,
            )
            i += 1

    for err in client.batch.failed_objects:
        logger.error(err.message)

    if quantization in ["pq", "sq"] and override == False:
        if quantization == "pq":
            if multivector is False:
                collection.config.update(
                    vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                        quantizer=wvc.Reconfigure.VectorIndex.Quantizer.pq(
                            segments=int(len(vectors[0]) / dim_to_seg_ratio),
                        )
                    )
                )
            else:
                collection.config.update(
                    vectorizer_config=[
                        wvc.Reconfigure.NamedVectors.update(
                            name="multivector",
                            vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                                quantizer=wvc.Reconfigure.VectorIndex.Quantizer.pq(
                                    segments=int(len(vectors[0][0]) / dim_to_seg_ratio),
                                )
                            ),
                        )
                    ]
                )
        elif quantization == "sq":
            if multivector is False:
                collection.config.update(
                    vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                        quantizer=wvc.Reconfigure.VectorIndex.Quantizer.sq()
                    )
                )
            else:
                collection.config.update(
                    vectorizer_config=[
                        wvc.Reconfigure.NamedVectors.update(
                            name="multivector",
                            vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                                quantizer=wvc.Reconfigure.VectorIndex.Quantizer.sq()
                            ),
                        )
                    ]
                )

        check_shards_readonly(collection)
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

                multivector_object = {}
                if multivector:
                    multivector_object["multivector"] = vector
                batch.add_object(
                    properties=data_object,
                    vector=vector if multivector is False else multivector_object,
                    uuid=uuid.UUID(int=i),
                    collection=class_name,
                )
                i += 1

        for err in client.batch.failed_objects:
            logger.error(err.message)

    logger.info("Waiting for vector indexing to finish")
    collection.batch.wait_for_vector_indexing()
    logger.info("Vector indexing finished")

    logger.info(f"Finished writing {len_objects} records")


def check_shards_readonly(collection: weaviate.collections.Collection):
    status = [s.status for s in collection.config.get_shards()]
    if not all(s == "READONLY" for s in status):
        raise Exception(f"shards are not READONLY at beginning: {status}")


def wait_for_all_shards_ready(collection: weaviate.collections.Collection):
    max_wait = 300
    before = time.time()

    while True:
        try:
            status = [s.status for s in collection.config.get_shards()]
        except Exception as e:
            logger.error(f"Error getting shards status: {e}")
            continue

        if all(s == "READY" for s in status):
            logger.debug(f"finished in {time.time()-before}s")
            return

        if time.time() - before > max_wait:
            raise Exception(f"after {max_wait}s not all shards READY: {status}")
        time.sleep(3)
