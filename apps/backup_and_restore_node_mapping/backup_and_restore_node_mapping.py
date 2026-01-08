import datetime
import numpy as np
import os
import random
import requests
import sys
import time
import uuid
import weaviate

from loguru import logger
from typing import Optional


def assert_expected_shard_count(client: weaviate.Client):
    expected = os.environ.get("EXPECTED_SHARD_COUNT")
    if expected is None or expected == "":
        expected = 1
    else:
        expected = int(expected)

    schema = client.schema.get()["classes"]
    class_shard_counts = [
        {"class": cls["class"], "actualCount": cls["shardingConfig"]["actualCount"]}
        for cls in schema
    ]

    logger.info(f"Expected shard count per class: {expected}")
    logger.info(f"Actual shard counts: {class_shard_counts}")

    assert len(class_shard_counts) > 0 and len(class_shard_counts) == len(schema)
    assert all(list(map(lambda cls: cls["actualCount"] == expected, class_shard_counts)))


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
            "properties": [
                {
                    "dataType": ["boolean"],
                    "name": "should_be_deleted",
                },
                {
                    "dataType": ["boolean"],
                    "name": "is_divisible_by_four",
                },
                {
                    "dataType": ["int"],
                    "name": "index_id",
                },
                {
                    "dataType": ["string"],
                    "name": "stage",
                },
            ],
        }

        client.schema.create_class(class_obj)

    assert_expected_shard_count(client)


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
    end=1000,
    stage="stage_0",
):
    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        delete_threshold = (end - start) * 0.1 + start
        for i in range(start, end):
            if i % 200 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{end}")
            data_object = {
                "should_be_deleted": i
                < delete_threshold,  # mark 10% of all records for future deletion
                "index_id": i,  # same as UUID, this way we can retrieve both using the primary key and the inverted index and make sure the results match
                "stage": stage,  # allows for setting filters that match the import stage
                "is_divisible_by_four": i % 4
                == 0,  # an arbitrary field that matches 1/4 of the dataset to allow filtered searches later on
            }

            vector = np.random.rand(32, 1)
            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )

        client.batch.wait_for_vector_indexing()
    logger.info(f"Finished writing {end-start} records")


def delete_records(client: weaviate.Client, class_name):
    client.batch.delete_objects(
        class_name=class_name,
        where={"operator": "Equal", "path": ["should_be_deleted"], "valueBoolean": True},
        output="minimal",
        dry_run=False,
    )


def fatal(msg):
    logger.error(msg)
    sys.exit(1)


def success(msg):
    logger.success(msg)


def validate_dataset(client: weaviate.Client, class_name):
    """Simple smoke test: verify data exists after restore"""
    result = client.query.aggregate(class_name).with_fields("meta { count }").do()

    logger.info(f"Aggregation result for {class_name}: {result}")

    if "data" not in result or "Aggregate" not in result["data"]:
        fatal(f"Invalid aggregation result for {class_name}: {result}")

    aggregate_data = result["data"]["Aggregate"]
    if class_name not in aggregate_data:
        fatal(
            f"Class {class_name} not found in aggregation result. Available: {list(aggregate_data.keys())}"
        )

    class_result = aggregate_data[class_name]
    logger.info(f"Class result for {class_name}: {class_result} (type: {type(class_result)})")

    if class_result is None:
        fatal(f"Class {class_name} has None result. Full result: {result}")

    if not isinstance(class_result, list):
        fatal(f"Class {class_name} result is not a list: {class_result}")

    if len(class_result) == 0:
        fatal(f"Class {class_name} has empty result list. Full result: {result}")

    total_count = class_result[0].get("meta", {}).get("count", 0)
    logger.info(f"Total count for {class_name}: {total_count}")

    if total_count == 0:
        fatal(f"Class {class_name} has 0 objects after restore. Full result: {result}")

    success(f"{class_name}: {total_count} objects found after restore")


def sample_range(start, end, size):
    return enumerate(random.sample(range(start, end), size))


def validate_queries(client: weaviate.Client, class_name):
    """Simple smoke test: verify basic queries work"""
    # Test simple Get query
    result = client.query.get(class_name, ["index_id"]).with_limit(5).do()
    if (
        "data" not in result
        or "Get" not in result["data"]
        or class_name not in result["data"]["Get"]
    ):
        fatal(f"Failed to query {class_name} after restore")
    if len(result["data"]["Get"][class_name]) == 0:
        fatal(f"No objects returned from {class_name} query")
    success(f"Successfully queried {class_name}")


def backend_provider():
    backend = os.environ.get("BACKUP_BACKEND_PROVIDER")
    if backend is None or backend == "":
        return "filesystem"
    return backend


def temp_backup_url_create():
    return f"http://localhost:8080/v1/backups/{backend_provider()}"


def temp_backup_url_create_status(backup_name):
    return f"http://localhost:8080/v1/backups/{backend_provider()}/{backup_name}"


def temp_backup_url_restore(backup_name):
    return f"http://localhost:8080/v1/backups/{backend_provider()}/{backup_name}/restore"


def temp_backup_url_restore_status(backup_name):
    return f"http://localhost:8080/v1/backups/{backend_provider()}/{backup_name}/restore"


def create_backup(client: weaviate.Client, name):
    create_body = {"id": name}
    res = requests.post(temp_backup_url_create(), json=create_body)
    if res.status_code > 399:
        fatal(f"Backup Create returned status code {res.status_code} with body: {res.json()}")

    while True:
        time.sleep(3)
        res = requests.get(temp_backup_url_create_status(name))
        res_json = res.json()
        if res_json["status"] == "SUCCESS":
            success(f"Backup creation successful")
            break
        if res_json["status"] == "FAILED":
            fatal(f"Backup failed with res: {res_json}")


def restore_backup(client: weaviate.Client, name, node_mapping: Optional[dict] = None):
    restore_body = {"id": name}
    if node_mapping:
        restore_body["node_mapping"] = node_mapping  # Use snake_case as per Weaviate API

    logger.info(f"Restoring backup with node mapping: {node_mapping}")
    logger.info(f"Restore request body: {restore_body}")
    res = requests.post(temp_backup_url_restore(name), json=restore_body)
    if res.status_code > 399:
        fatal(f"Backup Restore returned status code {res.status_code} with body: {res.json()}")

    while True:
        time.sleep(3)
        res = requests.get(temp_backup_url_restore_status(name))
        res_json = res.json()
        if res_json["status"] == "SUCCESS":
            success(f"Restore succeeded")
            break
        if res_json["status"] == "FAILED":
            fatal(f"Restore failed with res: {res_json}")


# Main test execution
import sys

# Check if we're in restore phase (passed as argument)
phase = sys.argv[1] if len(sys.argv) > 1 else "backup"

backup_name = os.environ.get(
    "BACKUP_NAME", f"{int(datetime.datetime.now().timestamp())}_node_mapping_test"
)

client = weaviate.Client("http://localhost:8080")

class_names = ["Class_A", "Class_B"]
objects_per_stage = 1000  # Reduced for quick smoke test
start_stage_1 = 0
end_stage_1 = objects_per_stage
expected_count_stage_1 = 0.9 * end_stage_1  # because of 10% deletions

if phase == "backup":
    logger.info("=== PHASE 1: BACKUP ===")
    logger.info(f"Step 0, reset everything, import schema")
    reset_schema(client, class_names)

    logger.info(f"Step 1, import objects across {len(class_names)} classes")
    for class_name in class_names:
        load_records(
            client,
            class_name,
            start=start_stage_1,
            end=end_stage_1,
            stage="stage_1",
        )

    logger.info("Step 2, delete 10% of objects to make sure deletes are covered")
    for class_name in class_names:
        delete_records(client, class_name)

    logger.info("Step 3, verify data exists before backup")
    for class_name in class_names:
        validate_dataset(client, class_name)

    logger.info("Step 4, create backup of current instance including all classes")
    create_backup(client, backup_name)
    logger.info(f"Backup created: {backup_name}")
    logger.info("Backup phase completed successfully!")

elif phase == "restore":
    logger.info("=== PHASE 2: RESTORE WITH NODE MAPPING ===")
    logger.info(
        f"Step 5, restore backup with node mapping (node1->new_node1, node2->new_node2, node3->new_node3)"
    )
    # Map original node names to new node names
    node_mapping = {"node1": "new_node1", "node2": "new_node2", "node3": "new_node3"}
    restore_backup(client, backup_name, node_mapping=node_mapping)

    # Wait a bit for restore to fully complete and schema to be available
    logger.info("Waiting for restore to fully complete...")
    time.sleep(5)

    # Verify schema was restored
    logger.info("Verifying schema was restored...")
    schema = client.schema.get()
    restored_classes = [cls["class"] for cls in schema.get("classes", [])]
    logger.info(f"Restored classes: {restored_classes}")

    for class_name in class_names:
        if class_name not in restored_classes:
            fatal(f"Class {class_name} was not restored. Available classes: {restored_classes}")

    logger.info("Step 6, verify data exists after restore")
    for class_name in class_names:
        validate_dataset(client, class_name)

    logger.info("Step 7, verify basic queries work after restore")
    for class_name in class_names:
        validate_queries(client, class_name)
    logger.info("Restore phase completed successfully!")

elif phase == "verify":
    logger.info("=== PHASE 3: VERIFY DATA PERSISTS AFTER RESTART ===")
    logger.info("Verifying data exists after cluster restart (no restore)")

    # Verify schema exists
    schema = client.schema.get()
    restored_classes = [cls["class"] for cls in schema.get("classes", [])]
    logger.info(f"Available classes: {restored_classes}")

    for class_name in class_names:
        if class_name not in restored_classes:
            fatal(f"Class {class_name} not found after restart")

    # Verify data exists
    logger.info("Verifying data exists after restart")
    for class_name in class_names:
        validate_dataset(client, class_name)

    # Verify queries work
    logger.info("Verifying queries work after restart")
    for class_name in class_names:
        validate_queries(client, class_name)

    logger.info("Data verification after restart completed successfully!")
    logger.info("Test completed successfully!")
else:
    fatal(f"Unknown phase: {phase}. Use 'backup', 'restore', or 'verify'")
