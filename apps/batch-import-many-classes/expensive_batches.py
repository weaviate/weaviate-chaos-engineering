import weaviate
from loguru import logger
from typing import Optional
import numpy as np
import uuid


def reset_schema(client: weaviate.Client):
    try:  # delete if present
        client.schema.delete_class("ExpensiveClass")
    except weaviate.UnexpectedStatusCodeException:
        pass
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {
            # use an  expensive ef construction to make this batch as slow as
            # possible (increases the chances of it blocking something)
            "efConstruction": 256,
            "maxConnections": 16,
            "ef": 256,
            "cleanupIntervalSeconds": 10,
        },
        "class": "ExpensiveClass",
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


def load_records(
    client: weaviate.Client,
    class_name,
    start=0,
    end=100_000,
):
    # large batch size increases likelihood of a slow batch
    client.batch.configure(batch_size=5000, callback=handle_errors)
    with client.batch as batch:
        for i in range(start, end):
            if i % 10000 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{end}")
            data_object = {
                "index_id": i,  # same as UUID, this way we can retrieve both using the primary key and the inverted index and make sure the results match
            }

            # many vector dimensions for the slowest possible import
            vector = np.random.rand(1536, 1)
            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {end-start} records")


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


client = weaviate.Client("http://localhost:8080", timeout_config=(20, 240))
reset_schema(client)
load_records(client, "ExpensiveClass", 0, 1_000_000)
