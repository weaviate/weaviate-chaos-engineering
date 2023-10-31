import weaviate
import weaviate.classes as wvc
from loguru import logger
from typing import Optional
import numpy as np
import uuid


def reset_schema(client: weaviate.WeaviateClient):
    try:  # delete if present
        client.collections.delete("ExpensiveClass")
    except weaviate.UnexpectedStatusCodeException:
        pass

    client.collections.create(
        name="ExpensiveClass",
        vectorizer_config=wvc.Configure.Vectorizer.none(),
        # use an  expensive ef construction to make this batch as slow as
        # possible (increases the chances of it blocking something)
        vector_index_config=wvc.Configure.vector_index(
            ef_construction=256,
            max_connections=16,
            ef=256,
            cleanup_interval_seconds=10,
        ),
        inverted_index_config=wvc.Configure.inverted_index(index_timestamps=True),
        properties=[
            wvc.Property(
                name="index_id",
                data_type=wvc.DataType.INT,
            ),
        ],
    )


def load_records(
    client: weaviate.WeaviateClient,
    class_name,
    start=0,
    end=100_000,
):
    # large batch size increases likelihood of a slow batch
    client.batch.configure(batch_size=5000, dynamic=True)
    with client.batch as batch:
        for i in range(start, end):
            if i % 10000 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{end}")
            data_object = {
                "index_id": i,  # same as UUID, this way we can retrieve both using the primary key and the inverted index and make sure the results match
            }

            # many vector dimensions for the slowest possible import
            vector = np.random.rand(1536, 1)
            batch.add_object(
                properties=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )

        if len(client.batch.failed_objects()) > 0:
            logger.error("Failed objects:")
            for failed in client.batch.failed_objects():
                logger.error(failed)

    logger.info(f"Finished writing {end-start} records")


client = weaviate.connect_to_local(timeout=(20, 240))
reset_schema(client)
load_records(client, "ExpensiveClass", 0, 1_000_000)
