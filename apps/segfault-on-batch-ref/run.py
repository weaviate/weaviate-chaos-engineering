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


def random_text(words: int):
    word_list = []
    for i in range(0, words):
        word_list.append(random.choice(random_words))
    return " ".join(word_list)


def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexType": "flat",
        "vectorIndexConfig": {
            "bq": {
                "enabled": True,
                "cache": True,
            }
        },
        "class": "Example1",
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "properties": [
            {
                "dataType": ["boolean"],
                "name": "bool_field",
            },
        ],
    }

    client.schema.create_class(class_obj)

    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {
            "skip": True,
        },
        "class": "Example2",
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "properties": [
            {
                "dataType": ["Example1"],
                "name": "ref",
            },
            {
                "dataType": ["string"],
                "name": "string_field",
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


def load_objects(client: weaviate.Client, ids_class_1, ids_class_2):
    client.batch.configure(batch_size=1000, callback=handle_errors)
    with client.batch as batch:
        for i, id in enumerate(ids_class_1):
            if i % 50 == 0:
                logger.info(f"Writing record {i}/{len(ids_class_1)}")
            batch.add_data_object(
                data_object={
                    "bool_field": random.choice([True, False]),
                },
                class_name="Example1",
                uuid=id,
            )

    logger.info(f"Finished writing {len(ids_class_1)} records for class 1")

    with client.batch as batch:
        for i, id in enumerate(ids_class_2):
            if i % 50 == 0:
                logger.info(f"Writing record {i}/{len(ids_class_2)}")
            batch.add_data_object(data_object={}, class_name="Example2", uuid=id)

    logger.info(f"Finished writing {len(ids_class_2)} records for class 1")


def load_references(client: weaviate.Client, iterations, ids_class_1, ids_class_2):
    client.batch.configure(batch_size=1000, callback=handle_errors)
    new_objects_per_iteration = 1000
    for i in range(0, iterations):
        logger.info(f"Iteration {i}:")
        logger.info(f"Add {new_objects_per_iteration} new objects to make the segments bigger")
        text = random_text(1000)
        with client.batch as batch:
            for j in range(new_objects_per_iteration):
                batch.add_data_object(
                    data_object={
                        "string_field": text,
                    },
                    class_name="Example2",
                )
        logger.info(f"Set all possible refs iteration")
        if i != 0 and i % 10 == 0:
            logger.info(f"Sleeping to force a flush which will lead to compactions")
            # time.sleep(random.randint(0,10))
            time.sleep(5)
        with client.batch as batch:
            for source_id in ids_class_2:
                for target_id in ids_class_1:
                    batch.add_reference(
                        from_object_uuid=source_id,
                        from_object_class_name="Example2",
                        from_property_name="ref",
                        to_object_uuid=target_id,
                        to_object_class_name="Example1",
                    )


def generate_ids(count: int):
    out = []
    for i in range(0, count):
        out.append(str(uuid.uuid4()))
    return out


if __name__ == "__main__":
    client = weaviate.Client("http://localhost:8080", timeout_config=int(30))

    object_count = 20

    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--action", default="import")
    args = parser.parse_args()

    if args.action == "import":
        ids_class_1 = generate_ids(object_count)
        ids_class_2 = generate_ids(object_count)

        load_objects(client, ids_class_1, ids_class_2)
        load_references(client, 400, ids_class_1, ids_class_2)
    elif args.action == "schema":
        reset_schema(client)
    else:
        logger.error("unknown --action option")
