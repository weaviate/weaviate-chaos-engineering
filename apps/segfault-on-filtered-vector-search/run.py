import weaviate
import datetime
import time
from loguru import logger
from typing import Optional
import random
import numpy as np
import argparse
import string


def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {
            "efConstruction": 32,
            "maxConnections": 4,
        },
        "class": "Example",
        "replicationConfig": {"asyncEnabled": True},
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "properties": [
            {
                "dataType": ["boolean"],
                "name": "bool_field",
            },
            {
                "dataType": ["string"],
                "name": "string_field",
            },
        ],
    }

    client.schema.create_class(class_obj)


# https://xkcd.com/221/
random_words = [
    "ZUxO",
    "wpzF",
    "Dexz",
    "sfaE",
    "nTxi",
    "Dtkw",
    "fgiO",
    "FnFV",
    "FNkW",
    "inkI",
    "zunb",
    "AaQg",
    "CdzQ",
    "PIaQ",
    "iPlx",
    "OzKq",
    "Yvbn",
    "QqeX",
    "lFGF",
    "cTRL",
]


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


def load_records(client: weaviate.Client, max_records=100000):
    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(max_records):
            if i % 1000 == 0:
                logger.info(f"Writing record {i}/{max_records}")
            batch.add_data_object(
                data_object={
                    "bool_field": random.choice([True, False]),
                    "string_field": random.choice(random_words),
                },
                vector=np.random.rand(32, 1),
                class_name="Example",
            )

            if i != 0 and i % 100000 == 0:
                logger.info(f"Completed {i} records, sleeping for 5s to force a flush")
                time.sleep(5)
    logger.info(f"Finished writing {max_records} records")


def query(client, count):
    for i in range(count):
        where_filter = {
            "operator": "And",
            "operands": [
                {
                    "path": ["bool_field"],
                    "operator": "Equal",
                    "valueBoolean": random.choice([True, False]),
                },
                {
                    "operator": "Or",
                    "operands": [
                        {
                            "path": ["string_field"],
                            "valueString": random.choice(random_words),
                            "operator": "Equal",
                        },
                        {
                            "path": ["string_field"],
                            "valueString": random.choice(random_words),
                            "operator": "Equal",
                        },
                        {
                            "path": ["string_field"],
                            "valueString": random.choice(random_words),
                            "operator": "Equal",
                        },
                    ],
                },
            ],
        }

        query_result = (
            client.query.get("Example", "_additional { id }")
            .with_where(where_filter)
            .with_limit(100)
            .do()
        )

        if i != 0 and i % 100 == 0:
            logger.info(f"sent {i} queries")


if __name__ == "__main__":
    client = weaviate.Client("http://localhost:8080", timeout_config=int(30))

    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--action", default="import")
    args = parser.parse_args()

    if args.action == "import":
        load_records(client, 1_000_000)
    elif args.action == "query":
        query(client, 10_000_000)
    elif args.action == "schema":
        reset_schema(client)
    else:
        logger.error("unknown --action option")
