import os
import random
from loguru import logger
from typing import Optional
import uuid
import weaviate
import weaviate.classes.config as wvc
import h5py
import time

CLASS_NAME = "Vector"


def get_muvera_config(multivector_implementation="regular"):
    if multivector_implementation == "regular":
        return None
    elif multivector_implementation == "muvera":
        return wvc.Configure.VectorIndex.MultiVector.Encoding.muvera()


def get_vectorizer_config(efC, m, multivector=False, multivector_implementation="regular"):
    if not multivector:
        vectorizer_config = wvc.Configure.Vectorizer.none()
    else:
        if multivector_implementation == "regular":
            muvera_config = None
        else:
            muvera_config = get_muvera_config(multivector_implementation)

        vectorizer_config = [
            wvc.Configure.NamedVectors.none(
                name="multivector",
                vector_index_config=wvc.Configure.VectorIndex.hnsw(
                    ef_construction=efC,
                    max_connections=m,
                    ef=-1,
                    multi_vector=wvc.Configure.VectorIndex.MultiVector.multi_vector(
                        encoding=muvera_config
                    ),
                ),
            )
        ]
    return vectorizer_config


def reset_schema(
    client: weaviate.WeaviateClient,
    efC,
    m,
    shards,
    distance,
    multivector=False,
    multivector_implementation="regular",
    index_type="hnsw",
):
    client.collections.delete_all()

    if index_type == "hnsw":
        vector_index_config = wvc.Configure.VectorIndex.hnsw(
            ef_construction=efC,
            max_connections=m,
            ef=-1,
            distance_metric=wvc.VectorDistances(distance),
        )
    elif index_type == "hfresh":
        vector_index_config = wvc.Configure.VectorIndex.hfresh(
            distance_metric=wvc.VectorDistances(distance),
        )
    else:
        logger.error(f"unknown index type {index_type}")
    client.collections.create(
        name=CLASS_NAME,
        vectorizer_config=get_vectorizer_config(efC, m, multivector, multivector_implementation),
        vector_index_config=vector_index_config if not multivector else None,
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
    multivector_implementation="regular",
    rq_bits=8,
    index_type="hnsw",
):
    collection = client.collections.get(CLASS_NAME)
    i = 0
    if vectors == None:
        vectors = [None] * 10_000_000
    batch_size = 100
    len_objects = len(vectors)

    with client.batch.fixed_size(batch_size=batch_size) as batch:
        for vector in vectors:
            if i == 100000 and quantization in ["pq", "sq", "bq", "rq"] and override == False:
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
                collection=CLASS_NAME,
            )
            i += 1

    for err in client.batch.failed_objects:
        logger.error(err.message)

    if index_type == "hnsw" and quantization in ["pq", "sq", "bq", "rq"] and override == False:
        if quantization == "pq":
            if multivector is False:
                collection.config.update(
                    vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                        quantizer=wvc.Reconfigure.VectorIndex.Quantizer.pq(
                            segments=int(len(vectors[0]) / dim_to_seg_ratio),
                        ),
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
                                ),
                            ),
                        )
                    ]
                )
        elif quantization == "sq":
            if multivector is False:
                collection.config.update(
                    vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                        quantizer=wvc.Reconfigure.VectorIndex.Quantizer.sq(),
                    )
                )
            else:
                collection.config.update(
                    vectorizer_config=[
                        wvc.Reconfigure.NamedVectors.update(
                            name="multivector",
                            vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                                quantizer=wvc.Reconfigure.VectorIndex.Quantizer.sq(),
                            ),
                        )
                    ]
                )
        elif quantization == "bq" and multivector is True:
            collection.config.update(
                vectorizer_config=[
                    wvc.Reconfigure.NamedVectors.update(
                        name="multivector",
                        vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                            quantizer=wvc.Reconfigure.VectorIndex.Quantizer.bq(),
                        ),
                    )
                ]
            )
        elif quantization == "rq":
            logger.info(f"Updating rq bits to {rq_bits}")
            if multivector is False:
                collection.config.update(
                    vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                        quantizer=wvc.Reconfigure.VectorIndex.Quantizer.rq(bits=rq_bits),
                    )
                )
            else:
                collection.config.update(
                    vectorizer_config=[
                        wvc.Reconfigure.NamedVectors.update(
                            name="multivector",
                            vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                                quantizer=wvc.Reconfigure.VectorIndex.Quantizer.rq(bits=rq_bits),
                            ),
                        )
                    ]
                )

    check_shards_readonly(collection)
    wait_for_all_shards_ready(client)

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
                collection=CLASS_NAME,
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
        logger.warning(f"shards are not READONLY at beginning: {status}")


def wait_for_all_shards_ready(client: weaviate.WeaviateClient, timeout=1200):
    collection = client.collections.get(CLASS_NAME)
    interval = 3
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

        if time.time() - before > timeout:
            logger.error(f"Shards not ready. Timeout reached")
            raise Exception(f"after {timeout}s not all shards READY: {status}")

        logger.info(f"Shards not ready. Waiting for {time.time() - before}s")
        time.sleep(interval)
