import random
from loguru import logger
from typing import Optional
import uuid
import weaviate
import h5py

class_name = "Vector"


def reset_schema(client: weaviate.Client, efC, m, shards, distance):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {
            "efConstruction": efC,
            "maxConnections": m,
            "ef": -1,  # will be overriden at query time
            "distance": distance,
        },
        "class": class_name,
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "properties": [],
        "shardingConfig": {
            "desiredCount": shards,
        },
    }

    client.schema.create_class(class_obj)


def handle_errors(results: Optional[dict]) -> None:
    """
    Handle error message from batch requests logs the message as an info message.
    Parameters
    ----------
    results : Optional[dict]
        The returned results for Batch creation.
    """

    if results is not None:
        for result in results:
            if (
                "result" in result
                and "errors" in result["result"]
                and "error" in result["result"]["errors"]
            ):
                for message in result["result"]["errors"]["error"]:
                    logger.error(message["message"])


def load_records(client: weaviate.Client, vectors, compression):
    client.batch.configure(batch_size=100, callback=handle_errors)
    i = 0
    if vectors == None:
        vectors = [None] * 10_000_000

    with client.batch as batch:
        for vector in vectors:
            if i % 10000 == 0:
                logger.info(f"writing record {i}/{len(vectors)}")

            if i == 100000 and compression == True:
                logger.info(f"pausing import to enable compression")
                break

            data_object = {
                "i": i,
            }

            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
            i += 1

    if compression == True:
        client.schema.update_config(
            class_name,
            {
                "vectorIndexConfig": {
                    "pq": {
                        "enabled": True,
                    }
                }
            },
        )

        i = 100000
        with client.batch as batch:
            while i < len(vectors):
                vector = vectors[i]
                if i % 10000 == 0:
                    logger.info(f"writing record {i}/{len(vectors)}")

                data_object = {
                    "i": i,
                }

                batch.add_data_object(
                    data_object=data_object,
                    vector=vector,
                    class_name=class_name,
                    uuid=uuid.UUID(int=i),
                )
                i += 1
    logger.info(f"Finished writing {len(vectors)} records")
