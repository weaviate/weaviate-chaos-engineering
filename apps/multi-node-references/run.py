import weaviate
import uuid
import random
import os
from typing import Optional
from loguru import logger


def do():
    targets = 200_000
    origin = os.getenv("origin") or "http://localhost:8080"
    client = weaviate.Client(origin)
    client.schema.delete_all()
    create_target_class(client)
    load_targets(client, 0, targets)

    create_source_class(client)
    load_sources(client, 0, 1_000_000, 5, targets)


def load_sources(client: weaviate.Client, start, end, ref_per_obj, targets):
    client.batch.configure(batch_size=10_000, callback=handle_errors)
    class_name = "Source"
    with client.batch as batch:
        for i in range(start, end):
            if i % 10000 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{end}")

            refs = [
                {
                    "beacon": f"weaviate://localhost/Target/{str(uuid.UUID(int=random.randint(0,targets-1)))}"
                }
                for i in range(ref_per_obj)
            ]

            data_object = {
                "index_id": i,
                "toTarget": refs,
            }

            batch.add_data_object(
                data_object=data_object,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {end-start} records")


def load_targets(client: weaviate.Client, start, end):
    client.batch.configure(batch_size=100, callback=handle_errors)
    class_name = "Target"
    with client.batch as batch:
        for i in range(start, end):
            if i % 10000 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{end}")
            data_object = {
                "index_id": i,
            }

            batch.add_data_object(
                data_object=data_object,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {end-start} records")


def create_target_class(client: weaviate.Client):
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {
            "efConstruction": 128,
            "maxConnections": 16,
            "ef": 256,
            "cleanupIntervalSeconds": 10,
        },
        "class": "Target",
        "invertedIndexConfig": {
            "indexTimestamps": True,
        },
        "properties": [
            {
                "dataType": ["int"],
                "name": "index_id",
            },
        ],
    }

    client.schema.create_class(class_obj)


def create_source_class(client: weaviate.Client):
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {
            "efConstruction": 128,
            "maxConnections": 16,
            "ef": 256,
            "cleanupIntervalSeconds": 10,
        },
        "class": "Source",
        "invertedIndexConfig": {
            "indexTimestamps": True,
        },
        "properties": [
            {
                "dataType": ["int"],
                "name": "index_id",
            },
            {
                "dataType": ["Target"],
                "name": "toTarget",
            },
        ],
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


do()
