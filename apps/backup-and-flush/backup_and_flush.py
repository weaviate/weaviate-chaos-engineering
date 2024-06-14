import datetime
import numpy as np
import os
import random
import sys
import time
import uuid
import weaviate

from loguru import logger
from typing import Optional


def other_classes(all_classes, self):
    return [c for c in all_classes if c != self]


def reset_schema(client: weaviate.Client, class_names):
    client.schema.delete_all()
    for class_name in class_names:
        class_obj = {
            "vectorizer": "none",
            "vectorIndexConfig": {
                "efConstruction": 128,
                "maxConnections": 16,
                "ef": 256,
                "cleanupIntervalSeconds": 10,
            },
            "class": class_name,
            "invertedIndexConfig": {
                "indexTimestamps": False,
            },
            "replicationConfig": {
                "asyncEnabled": True},
            "properties": [
                {
                    "dataType": ["boolean"],
                    "name": "is_divisible_by_four",
                },
                {
                    "dataType": ["int"],
                    "name": "index_id",
                },
            ],
        }

        client.schema.create_class(class_obj)

    for class_name in class_names:
        for other in other_classes(class_names, class_name):
            add_prop = {
                "dataType": [
                    other,
                ],
                "name": f"to_{other}",
            }

            client.schema.property.create(class_name, add_prop)


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
    client: weaviate.Client,
    class_name="Class",
    start=0,
    end=100_000,
    stage="stage_0",
    all_classes: Optional[list[str]] = None,
):
    if all_classes is None:
        all_classes = []

    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(start, end):
            if i % 10000 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{end}")
            data_object = {
                "index_id": i,  # same as UUID, this way we can retrieve both using the primary key and the inverted index and make sure the results match
                "is_divisible_by_four": i % 4
                == 0,  # an arbitrary field that matches 1/4 of the dataset to allow filtered searches later on
            }

            vector = np.random.rand(1, 1536)[0].tolist()
            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {end-start} records")


def fatal(msg):
    logger.error(msg)
    sys.exit(1)


def success(msg):
    logger.success(msg)


def backend_provider():
    backend = os.environ.get("BACKUP_BACKEND_PROVIDER")
    if backend is None or backend == "":
        return "filesystem"
    return backend


def create_backup(client: weaviate.Client, name):

    res = client.backup.create(
        backup_id=name,
        backend=backend_provider(),
        wait_for_completion=True,
    )
    if res["status"] != "SUCCESS":
        fatal(f"Backup Create failed: {res}")


def get_shard_name(client: weaviate.Client, class_name: str):
    nodes = client.cluster.get_nodes_status(output="verbose")

    return nodes[0]["shards"][0]["name"]


def check_wal_flushed(client: weaviate.Client, class_name: str, timestamp: datetime.datetime):
    # Mapped volume to have access to Weaviate's data directory from container
    # if running this script locally replace with ../apps/weaviate/data
    weaviate_dir = "/data"

    shard_name = get_shard_name(client, class_name)
    objects_dir = f"{weaviate_dir}/{class_name.lower()}/{shard_name}/lsm/objects"
    files = os.listdir(objects_dir)
    for file in files:
        if file.endswith(".wal"):
            file_path = os.path.join(objects_dir, file)
            file_size = os.path.getsize(file_path)
            file_size_mb = file_size / (1024 * 1024)
            creation_time = os.path.getctime(file_path)
            dt_creation_time = datetime.datetime.fromtimestamp(creation_time)
            print(f"File: {file}, Size: {file_size_mb} bytes, Created: {dt_creation_time}")
            delay = dt_creation_time - timestamp
            if file_size_mb == 0.0:
                logger.info(
                    f"WAL file was flushed after {default_flush_timeout} seconds of its creation."
                )
            else:
                if delay > datetime.timedelta(seconds=default_flush_timeout):
                    fatal(
                        f"WAL file {file_path} was not flushed since more than {default_flush_timeout} seconds. WAL is {delay.total_seconds()} seconds old and has a size of {file_size_mb} MB"
                    )
                else:
                    fatal(
                        f"Sleeping for {default_flush_timeout} seconds was not enough to flush the WAL file {file_path}. WAL is {delay.total_seconds()} seconds old and has a size of {file_size_mb} MB"
                    )


client = weaviate.Client("http://localhost:8080")

backup_name = f"{int(datetime.datetime.now().timestamp())}"

class_names = ["Vector"]
expected_count = 100_000
default_flush_timeout = 60

logger.info(f"Step 0, reset everything, import schema")
reset_schema(client, class_names)

logger.info(f"Step 1, import objects across {len(class_names)} classes")
for class_name in class_names:
    load_records(
        client,
        class_name,
        start=0,
        end=50_000,
        stage="stage_1",
        all_classes=class_names,
    )

logger.info("Step 2, create backup of current instance including all classes")
create_backup(client, backup_name)

# Get a timestamp on when the second import starts
start_import_timestamp = datetime.datetime.now()

logger.info(f"Step 3, import second half of objects across {len(class_names)} classes")
for class_name in class_names:
    load_records(
        client,
        class_name,
        start=50_000,
        end=100_000,
        stage="stage_2",
        all_classes=class_names,
    )


# Add 10 extra seconds to make sure we give enough time (depending on when 1st flush callback was invoked)
default_flush_timeout += 10
logger.info(f"Step 4, sleep for {default_flush_timeout} seconds to allow WAL to flush")
time.sleep(default_flush_timeout)

logger.info(f"Step 5, verify that the WAL is flushed")
for class_name in class_names:
    check_wal_flushed(client, class_name, start_import_timestamp)
