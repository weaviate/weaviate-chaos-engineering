import random
from loguru import logger
from typing import Optional
import uuid
import weaviate

def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig":{
            "skip": True
        },
        "class": "Set",
        "invertedIndexConfig":{
            "indexTimestamps":False,
        },
        "properties": [
            {
                "dataType": [ "boolean" ],
                "name": "prop_1",
            },
            {
                "dataType": [ "boolean" ],
                "name": "prop_2",
            },
            {
                "dataType": [ "int" ],
                "name": "modulo_11",
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

def load_records(client: weaviate.Client, start=0, end=100_000):
    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(start, end):
            if i % 10000 == 0:
                logger.info(f"writing record {i}/{end}")
            data_object={
                "prop_1": True,
                "prop_2": i % 11 < 10,
                "modulo_11": i%11,
            }

            batch.add_data_object(
                data_object=data_object,
                class_name="Set",
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {end-start} records")

client = weaviate.Client("http://localhost:8080")

reset_schema(client)
load_records(client, 0, 10_000_000)

