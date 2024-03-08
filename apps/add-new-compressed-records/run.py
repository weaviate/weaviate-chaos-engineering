import os
import weaviate
import uuid
import numpy as np

from loguru import logger
from typing import Optional


num_objects = 150000


def reset_schema(client: weaviate.Client, class_names):
    client.schema.delete_all()
    for class_name in class_names:
        class_obj = {
            "vectorizer": "none",
            "vectorIndexConfig": {
                "efConstruction": 128,
                "maxConnections": 16,
                "ef": 256,
                "cleanupIntervalSeconds": 10,
                "pq": {"enabled": False, "trainingLimit": 100000},  # Enable PQ
            },
            "class": class_name,
            "invertedIndexConfig": {
                "indexTimestamps": False,
            },
            "properties": [
                {"dataType": ["string"], "name": "name"},
                {"dataType": ["int"], "name": "index"},
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


def load_records(client: weaviate.Client, class_name="Class"):
    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(num_objects):
            if i % 100 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{num_objects}")
            data_object = {"name": f"object#{i}", "index": i}
            vector = np.random.rand(32, 1)
            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {num_objects} records")


def update_schema(client: weaviate.Client, class_names):
    for class_name in class_names:
        class_obj = {
            "vectorizer": "none",
            "vectorIndexConfig": {
                "efConstruction": 128,
                "maxConnections": 16,
                "ef": 256,
                "cleanupIntervalSeconds": 10,
                "pq": {"enabled": True, "trainingLimit": 100000},  # Enable PQ
            },
            "class": class_name,
            "invertedIndexConfig": {
                "indexTimestamps": False,
            },
        }
        client.schema.update_config(class_name, class_obj)


def load_new_records(client: weaviate.Client, class_name="Class"):
    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(num_objects):
            if i % 100 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{num_objects}")
            data_object = {"name": f"object#{i} has string", "index": i + 2}
            vector = np.random.rand(32, 1)
            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished updating {num_objects} records")


client = weaviate.Client("http://localhost:8080")

class_names = ["Class_T", "Class_U"]
reset_schema(client, class_names)

logger.info("Inserting data...")
for class_name in class_names:
    load_records(client, class_name)

logger.info("Compress vectors on all classes...")
class_names = ["Class_T", "Class_U"]
update_schema(client, class_names)

logger.info("Inserting new records on classes with PQ...")
for class_name in class_names:
    load_new_records(client, class_name)

logger.info("Test complete!")
