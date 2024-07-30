import random
import numpy as np
from loguru import logger
from typing import Optional
import uuid
import weaviate


def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexType": "flat",
        "vectorIndexConfig": {
            # super weak params, hnsw accuracy doesn't matter, prefer import
            # speed in this scenario
            "efConstruction": 32,
            "maxConnections": 4,
            "ef": 32,
            "bq": {
                "enabled": True,
                "cache": True,
            },
        },
        "class": "OneLeakyBoy",
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "properties": [
            {
                "dataType": ["int"],
                "name": "modulo_10000",
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


def load_records(client: weaviate.Client, size: int):
    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(0, size):
            # wait with querying until we have at least 300k objects imported,
            # otherwise the querying will just slow imports down. For the bug
            # to occur, we need at 100s of thousands of objects, so there is
            # little point in querying on an almost empty cluster. Then once
            # querying started, send one filter about every x imports. where x
            # is picked so that it OOMs quickly with the bug, but can still
            # import fast enough without the bug
            if i == 300_000:
                logger.info(f"start querying")

            if i > 300_000 and i % 100 == 0:
                send_filter_query(client)

            if i % 10000 == 0:
                logger.info(f"writing record {i}/{size}")
            data_object = {
                "modulo_10000": i % 10000,
            }

            batch.add_data_object(
                data_object=data_object,
                vector=np.random.rand(32, 1),
                class_name="OneLeakyBoy",
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {size} records")


def send_filter_query(client: weaviate.Client):
    where_filter = {
        "operator": "And",
        "operands": [
            {
                "operator": "And",
                "operands": [
                    {"valueInt": 56, "operator": "GreaterThanEqual", "path": ["modulo_10000"]},
                    {"valueInt": 57, "operator": "LessThanEqual", "path": ["modulo_10000"]},
                ],
            },
            {
                "operator": "And",
                "operands": [
                    {"valueInt": 12, "operator": "GreaterThanEqual", "path": ["modulo_10000"]},
                    {"valueInt": 13, "operator": "LessThanEqual", "path": ["modulo_10000"]},
                ],
            },
        ],
    }

    near_object = {"id": str(uuid.UUID(int=1))}
    q = (
        client.query.get("OneLeakyBoy", "_additional{ id }")
        .with_where(where_filter)
        .with_limit(10)
        .with_near_object(near_object)
    )
    query_result = q.do()


client = weaviate.Client("http://localhost:8080")

reset_schema(client)
load_records(client, 500_000)
