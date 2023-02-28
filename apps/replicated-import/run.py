import weaviate
import datetime
import time
from loguru import logger
from typing import Optional
import random
import numpy as np
import argparse
import string
import uuid
import sys

import_error_count = 0


def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {
            "efConstruction": 64,
            "maxConnections": 8,
        },
        "class": "Document",
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "replicationConfig": {"factor": 3},
        "properties": [
            {
                "dataType": ["text"],
                "name": "content",
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
                    global import_error_count
                    import_error_count += 1
                    logger.error(message["message"])


def load_objects(client: weaviate.Client, size: int):
    client.batch.configure(
        batch_size=50,
        callback=handle_errors,
        dynamic=False,
        num_workers=8,
        consistency_level="QUORUM",
    )
    with client.batch as batch:
        for i in range(size):
            if i % 1000 == 0:
                logger.info(f"Writing record {i}/{size}")
            batch.add_data_object(
                data_object={
                    "content": f"some content for object {i}",
                },
                class_name="Document",
                uuid=uuid.UUID(int=i),
                vector=np.random.rand(32, 1),
            )

    logger.info(f"Finished writing {size} records with {import_error_count} errors")


# the idea is that every object has to be returned correctly since we had at
# most one node death, so a quorum must always work. The assumption is that any
# write request could either be written to at least two nodes succesfully – or
# if it could not be written – has been repeated client-side
def validate_objects(client: weaviate.Client, max_id: int):
    random_picks = 100_000
    missing_objects = 0
    errors = 0

    for i in range(random_picks):
        obj_id = uuid.UUID(int=random.randint(0, max_id))
        try:
            data_object = client.data_object.get_by_id(
                uuid=obj_id, class_name="Document", consistency_level="QUORUM"
            )
            if data_object is None:
                missing_objects += 1
        except Exception as e:
            errors += 1
            logger.error(e)
        if i % 1000 == 0:
            logger.info(f"validated {i}/{random_picks} random objects")

    logger.info(
        f"Finished validation with {missing_objects} missing objects and {errors} errors"
    )
    if errors > 0 or missing_objects > 0:
        logger.error("Failed!")
        sys.exit(1)


# def load_references(client: weaviate.Client, iterations, ids_class_1, ids_class_2):
#     client.batch.configure(batch_size=1000, callback=handle_errors)
#     new_objects_per_iteration=1000
#     for i in range(0, iterations):
#         logger.info(f"Iteration {i}:")
#         logger.info(f"Add {new_objects_per_iteration} new objects to make the segments bigger")
#         text=random_text(1000)
#         with client.batch as batch:
#             for j in range(new_objects_per_iteration):
#                 batch.add_data_object(
#                     data_object={
#                         "string_field": text,
#                     },
#                     class_name="Example2"
#                 )
#         logger.info(f"Set all possible refs iteration")
#         if i != 0 and i%10 == 0:
#             logger.info(f"Sleeping to force a flush which will lead to compactions")
#             # time.sleep(random.randint(0,10))
#             time.sleep(5)
#         with client.batch as batch:
#                 for source_id in ids_class_2:
#                     for target_id in ids_class_1:
#                         batch.add_reference(
#                           from_object_uuid=source_id,
#                           from_object_class_name="Example2",
#                           from_property_name="ref",
#                           to_object_uuid=target_id,
#                           to_object_class_name="Example1",
#                         )


if __name__ == "__main__":
    client = weaviate.Client("http://localhost:8080", timeout_config=int(30))
    object_count = 300000

    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--action", default="import")
    args = parser.parse_args()

    if args.action == "import":
        load_objects(client, object_count)
        # load_references(client, 400, ids_class_1, ids_class_2)
        validate_objects(client, object_count - 1)
    elif args.action == "schema":
        reset_schema(client)
    else:
        logger.error("unknown --action option")
