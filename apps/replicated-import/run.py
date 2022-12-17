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

def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig":{
            "efConstruction":64,
            "maxConnections":8,
        },
        "class": "Document",
        "invertedIndexConfig":{
            "indexTimestamps":False,
        },
        "shardingConfig": {
            "replicas": 3 # TODO: adjust to new API when ready!
        },
        "properties": [
            {
                "dataType": [ "text" ],
                "name": "content",
            },
        ]
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
                'result' in result
                and 'errors' in result['result']
                and 'error' in result['result']['errors']
            ):
                for message in result['result']['errors']['error']:
                    logger.error(message['message'])

def load_objects(client: weaviate.Client, size: int):
    client.batch.configure(
            batch_size=10,
            callback=handle_errors,
            dynamic=True,
            num_workers=8,
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
                vector=np.random.rand(32,1),
            )

    logger.info(f"Finished writing {size} records")

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
    object_count=300000
    reset_schema(client)
    load_objects(client, object_count)
    # load_references(client, 400, ids_class_1, ids_class_2)
