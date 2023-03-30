import weaviate
import uuid
from loguru import logger
from typing import Optional
import numpy as np
import sys

client = weaviate.Client("http://localhost:8080")

total_objects = 24_000
# number of levels indicate the number of classes. The idea is that the amount
# of objects is like a pyramid. Level 0 has the most elements. Level 1 has a
# fraction of level 0, and so on.
levels = 2
# multiplier controls the ratio of amount of objects between levels, e.g. Level
# 1 has 1/2 of the amount of objects of Level 0, etc.
multiplier = 2
# every class has an outgoing ref to the "closest" object of the next level.
# E.g. Items 0-1 of level 0 all point to item 0 of level 1, etc.


def reset_schema(client: weaviate.Client, levels):
    client.schema.delete_all()

    for level in range(levels - 1, -1, -1):
        class_obj = {
            "vectorizer": "none",
            "class": f"Level_{level}",
            "invertedIndexConfig": {
                "indexTimestamps": False,
            },
            "properties": [
                {
                    "dataType": ["int"],
                    "name": "level",
                },
                {
                    "dataType": ["int"],
                    "name": "index_id_int",
                },
                {
                    "dataType": ["int"],
                    "name": "index_id_mod1000_int",
                },
            ],
        }

        if level == 0:
            class_obj["vectorIndexConfig"] = {
                "efConstruction": 128,
                "maxConnections": 16,
                "ef": 256,
                "cleanupIntervalSeconds": 10,
            }
        else:
            class_obj["vectorIndexConfig"] = {"skip": True}

        if level != levels - 1:
            class_obj["properties"].append(
                {
                    "dataType": [f"Level_{level+1}"],
                    "name": f"to_Level_{level+1}",
                }
            )

        client.schema.create_class(class_obj)


def load_records(client: weaviate.Client, count: int):
    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(0, count):
            if i % 5000 == 0:
                logger.info(f"Writing record {i}/{count}")
            add_object_at_level(batch, i, 0)
    logger.info(f"Finished writing {count} records")


def add_object_at_level(batch, total_id: int, level: int):
    if level < levels - 1 and total_id % multiplier == 0:
        add_object_at_level(batch, int(total_id / multiplier), level + 1)

    vector = None
    if level == 0:
        vector = np.random.rand(32, 1)

    batch.add_data_object(
        data_object={
            "level": level,
            "index_id_int": total_id,
            "index_id_mod1000_int": total_id % 1000,
        },
        vector=vector,
        class_name=f"Level_{level}",
        uuid=uuid.UUID(int=total_id),
    )

    if level != levels - 1:
        batch.add_reference(
            from_object_uuid=str(uuid.UUID(int=total_id)),
            from_object_class_name=f"Level_{level}",
            from_property_name=f"to_Level_{level+1}",
            to_object_uuid=str(uuid.UUID(int=int(total_id / multiplier))),
            to_object_class_name=f"Level_{level+1}",
        )


def query(client: weaviate.client):
    where_filter = {
        "operator": "And",
        "operands": [
            {
                "path": ["to_Level_1", "Level_1", "level"],
                "operator": "Equal",
                "valueInt": 1,
            },
            {
                "path": ["to_Level_1", "Level_1", "index_id_mod1000_int"],
                "operator": "Equal",
                "valueInt": 0,
            },
        ],
    }

    query_result = (
        client.query.get("Level_0", "_additional { id }")
        .with_where(where_filter)
        .with_limit(10000)
        .do()
    )

    total_count = len(query_result["data"]["Get"]["Level_0"])
    expected_total_count = int((total_objects / 2) / 1000 * 2)

    if total_count != expected_total_count:
        logger.error(f"Level_0: got {total_count} objects, wanted {expected_total_count}")
        sys.exit(1)
    else:
        logger.success(f"Level_0: got {total_count} objects, wanted {expected_total_count}")


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


reset_schema(client, levels)
load_records(client, total_objects)
query(client)
