import random
from loguru import logger
from typing import Optional
import uuid
import weaviate


def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {"skip": True},
        "class": "GeoClass",
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "replicationConfig": {
            "asyncEnabled": True,
        },
        "properties": [
            {
                "dataType": ["geoCoordinates"],
                "name": "location",
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


locations = [
    {"latitude": 57.139723, "longitude": -2.110075},
    {"latitude": 57.139435, "longitude": -2.109959},
    {"latitude": 57.138994, "longitude": -2.111395},
    {"latitude": 57.139649, "longitude": -2.112074},
    {"latitude": 57.139424, "longitude": -2.112388},
    {"latitude": 57.139235, "longitude": -2.112618},
    {"latitude": 57.134827, "longitude": -2.110457},
    {"latitude": 57.138734, "longitude": -2.110469},
    {"latitude": 57.139001, "longitude": -2.112882},
    {"latitude": 57.135967, "longitude": -2.111105},
    {"latitude": 57.136712, "longitude": -2.111339},
    {"latitude": 57.138148, "longitude": -2.11245},
    {"latitude": 57.137951, "longitude": -2.112581},
    {"latitude": 57.138887, "longitude": -2.109775},
    {"latitude": 57.142311, "longitude": -2.108976},
    {"latitude": 57.146602, "longitude": -2.090165},
    {"latitude": 57.139849, "longitude": -2.109497},
    {"latitude": 57.140407, "longitude": -2.108673},
    {"latitude": 57.139545, "longitude": -2.108208},
]


def load_records(client: weaviate.Client, start=0, end=100_000):
    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(start, end):
            if i % 10000 == 0:
                logger.info(f"writing record {i}/{end}")
            data_object = {"location": random.choice(locations)}

            batch.add_data_object(
                data_object=data_object,
                class_name="GeoClass",
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {end-start} records")


client = weaviate.Client("http://localhost:8080")

for i in range(0, 5):
    logger.info(f"Iteration {i}")
    reset_schema(client)
    load_records(client, 0, 50_000)
