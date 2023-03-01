import weaviate
import datetime
import time
from loguru import logger
from typing import Optional
import random
import numpy as np
from uuid import uuid1


def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {
            "efConstruction": 64,
            "maxConnections": 4,
            "cleanupIntervalSeconds": 10,
        },
        "class": "Example",
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "properties": [
            {
                "dataType": ["boolean"],
                "name": "bool_field",
            },
            {
                "dataType": ["int"],
                "name": "field_1",
            },
            {
                "dataType": ["int"],
                "name": "field_2",
            },
            {
                "dataType": ["int"],
                "name": "field_3",
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


def load_records(client: weaviate.Client, max_records=100000, update_percent=30):
    # some uuids for reuse to force updates
    uuids = [str(uuid1()) for _ in range(100)]

    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(max_records):
            if i % 1000 == 0:
                logger.info(f"Writing record {i}/{max_records}")
            data_object = {
                "bool_field": True,
                "field_1": random.randint(0, 1e12),
                "field_2": random.randint(0, 1e12),
                "field_3": random.randint(0, 1e12),
            }
            vector = np.random.rand(32, 1)
            if random.randint(0, 100) > update_percent:
                batch.add_data_object(
                    data_object=data_object,
                    vector=vector,
                    uuid=random.choice(uuids),
                    class_name="Example",
                )
            else:
                batch.add_data_object(data_object=data_object, vector=vector, class_name="Example")
    logger.info(f"Finished writing {max_records} records")


client = weaviate.Client("http://localhost:8080")

logger.info(f"30 iterations")
for i in range(30):
    count = random.randint(10_000, 150_000)
    logger.info(f"iteration {i} with count {count}")
    reset_schema(client)
    load_records(client, count)
