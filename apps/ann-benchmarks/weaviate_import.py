import random
from loguru import logger
from typing import Optional
import uuid
import weaviate
import h5py
import time

class_name = "Vector"


def reset_schema(client: weaviate.Client, efC, m, shards, distance):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {
            "efConstruction": efC,
            "maxConnections": m,
            "ef": -1,  # will be overriden at query time
            "distance": distance,
        },
        "class": class_name,
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "properties": [],
        "shardingConfig": {
            "desiredCount": shards,
        },
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


def load_records(
    client: weaviate.Client, vectors, compression, dim_to_seg_ratio, override, duplicates
):
    client.batch.configure(batch_size=100, callback=handle_errors)
    i = 0
    if vectors == None:
        vectors = [None] * 10_000_000

    with client.batch as batch:
        for vector in vectors:
            if i % 10000 == 0:
                logger.info(f"writing record {i}/{len(vectors)}")

            if i == 100000 and compression == True and override == False:
                logger.info(f"pausing import to enable compression")
                break

            if duplicates["enabled"] == True and i != 0 and i % duplicates["every"] == 0:
                insert_duplicates(client, vectors, i, duplicates["count"])

            data_object = {
                "i": i,
            }

            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
            i += 1

    if compression == True and override == False:
        client.schema.update_config(
            class_name,
            {
                "vectorIndexConfig": {
                    "pq": {
                        "enabled": True,
                        "segments": int(len(vectors[0]) / dim_to_seg_ratio),
                    }
                }
            },
        )

        wait_for_all_shards_ready(client)

        i = 100000
        with client.batch as batch:
            while i < len(vectors):
                vector = vectors[i]
                if i % 10000 == 0:
                    logger.info(f"writing record {i}/{len(vectors)}")

                data_object = {
                    "i": i,
                }

                batch.add_data_object(
                    data_object=data_object,
                    vector=vector,
                    class_name=class_name,
                    uuid=uuid.UUID(int=i),
                )

                if duplicates["enabled"] == True and i != 0 and i % duplicates["every"] == 0:
                    insert_duplicates(client, vectors, i, duplicates["count"])

                i += 1
    logger.info(f"Finished writing {len(vectors)} records")


def insert_duplicates(client: weaviate.Client, vectors, max_id, count):
    ids = random.sample(range(max_id), count)
    with client.batch as batch:
        for vid in ids:
            data_object = {
                "i": vid,
            }

            batch.add_data_object(
                data_object=data_object,
                vector=vectors[vid],
                class_name=class_name,
                uuid=uuid.UUID(int=vid),
            )
    logger.info(f"Inserted {count} duplicates (randomly selected from id 0..{max_id})")


def wait_for_all_shards_ready(client: weaviate.Client):
    status = [s["status"] for s in client.schema.get_class_shards(class_name)]
    if not all(s == "READONLY" for s in status):
        raise Exception(f"shards are not READONLY at beginning: {status}")

    max_wait = 300
    before = time.time()

    while True:
        time.sleep(3)
        status = [s["status"] for s in client.schema.get_class_shards(class_name)]
        if all(s == "READY" for s in status):
            logger.info(f"finished in {time.time()-before}s")
            return

        if time.time() - before > max_wait:
            raise Exception(f"after {max_wait}s not all shards READY: {status}")
