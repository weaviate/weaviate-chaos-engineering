import random
from loguru import logger
from typing import Optional
import uuid
import weaviate
import time
import sys


def forced_panic_scenario(client: weaviate.Client):
    # sroar bug causes failure in compaction due to
    # size of internal container not being set correctly in relation
    # to number of elements it holds

    # This test aims to prevent regression on the issue
    # https://github.com/weaviate/sroar/issues/1

    # Test creates 4 segments to be finally compated to one.
    # Writes to 2 segments enough elements to create 2nd
    # internal container for docIDs
    # segment#1 and segment#2 will be compacted to one,
    # having 1..70k additions and 0 deletions

    # Next 2 segments: segment#3 and segment#4 will be compacted to one,
    # having failing deletion bitmap.
    # First broken bitmap is created when internal array (thus 300 elements)
    # is AndNot-ed with internal bitmap (thus 3000 elements).
    # While broken bitmap is still usable, it is empty bitmap Or-ed with broken
    # bitmap that produces failing bitmap, when accesses via methods using
    # internally sroar.array.all().
    # Or operation between empty bitmap and broken bitmap is performend in
    # Condense method reducing size of resultant bitmap, so failing bitmap is
    # stored to compacted segment #3_4.
    # Attempt of merging segment #1_2 and #3_4 ends with panic, due to
    # failing Deletions of #3_4 being accessed by compaction process

    logger.info("STARTED forced_panic_scenario")
    sleep = 2

    # segment 1
    create_from_to(client, 1, 33_000)
    wait_for_flush(sleep)

    # segment 2
    create_from_to(client, 33_000, 66_000)
    wait_for_flush(sleep)

    # segment 3
    delete_from_to(client, 65_800, 66_000)
    wait_for_flush(sleep)

    # segment 4
    create_from_to(client, 66_000, 69_000)
    wait_for_flush(sleep)

    # wait for compaction #3 + #4, then #1_2 + #3_4 to happen
    wait_for_compactions(4)

    # if weaviate panics, getting object will fail
    try:
        id = 68_000
        logger.info(f"sanity check - fetching object {id}")
        client.data_object.get_by_id(str(uuid.UUID(int=id)), class_name="Set")
    except Exception as e:
        logger.error("Exception occurred on getting object:", e)
        exit(1)
    except:
        logger.error("Error occurred on getting object:", sys.exc_info()[0])
        exit(1)

    logger.info("FINISHED forced_panic_scenario")


def randomized_panic_scenario(client: weaviate.Client):
    logger.info("STARTED randomized_panic_scenario")

    sleep = 2
    created = 0
    count_objects = 2_500_000

    while created < count_objects:
        if random.random() < 0.5:
            # make a really small additions
            create_delta = random.randrange(1, 2000)
        else:
            # make a really large additions
            create_delta = random.randrange(5000, 10000)

        create_from = created + 1
        create_to = create_from + create_delta
        create_from_to(client, create_from, create_to)
        wait_for_flush(sleep)

        if random.random() < 0.25:
            if random.random() < 0.5:
                # make a really small deletions
                delete_delta = random.randrange(1, 500)
            else:
                # make a really large deletions
                delete_delta = random.randrange(2500, 5000)

            # can not delete more than it is already created
            delete_from = max(1, create_to - delete_delta)
            delete_to = create_to
            delete_from_to(client, delete_from, delete_to)

        created += create_delta

    wait_for_flush(sleep)
    logger.info(f"Created {created} objects")

    logger.info("FINISHED randomized_panic_scenario")


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


def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {},
        "class": "Set",
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "replicationConfig": {"asyncEnabled": True},
        "properties": [
            {
                "dataType": ["boolean"],
                "name": "bool",
            },
            {
                "dataType": ["boolean"],
                "name": "bool_modulo",
            },
            {
                "dataType": ["int"],
                "name": "modulo_31",
            },
        ],
    }
    client.schema.create_class(class_obj)


def create_from_to(client: weaviate.Client, fromInc: int, toExc: int):
    logger.info(f"creating objects [{fromInc}-{toExc})")

    with client.batch as batch:
        for i in range(fromInc, toExc):
            batch.add_data_object(
                data_object=create_object(i),
                class_name="Set",
                uuid=uuid.UUID(int=i),
            )
        batch.flush()

    logger.info(f"created {toExc-fromInc} objects")


def delete_from_to(client: weaviate.Client, fromInc: int, toExc: int):
    logger.info(f"deleting objects [{fromInc}-{toExc})")

    deleted = 0
    skipped = 0
    for i in range(fromInc, toExc):
        try:
            client.data_object.delete(str(uuid.UUID(int=i)), "Set")
            deleted += 1
        except Exception as e:
            skipped += 1

    logger.info(f"deleted {deleted} objects, skipped {skipped}")


def create_object(i: int):
    data_object = {
        "bool": True,
        "bool_modulo": i % 31 < 23,
        "modulo_31": i % 31,
    }
    return data_object


def wait_for_flush(sleep: int):
    logger.info(f"sleeping {sleep}s to flush segment")
    time.sleep(sleep)


def wait_for_compactions(sleep: int):
    logger.info(f"sleeping {sleep}s to compact segments")
    time.sleep(sleep)


client = weaviate.Client("http://localhost:8080")
client.batch.configure(batch_size=100, callback=handle_errors)

reset_schema(client)

forced_panic_scenario(client)
randomized_panic_scenario(client)
