import datetime
import numpy as np
import os
import random
import requests
import sys
import time
import uuid
import weaviate
from weaviate import Tenant, schema

from loguru import logger
from typing import Optional, List

WEAVIATE_PORT = 8080


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
            "multiTenancyConfig": {"enabled": True},
            "replicationConfig": {
                "factor": 2,
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

    for class_name in class_names:
        for other in other_classes(class_names, class_name):
            add_prop = {
                "dataType": [
                    other,
                ],
                "name": f"to_{other}",
            }

            client.schema.property.create(class_name, add_prop)

    #assert_expected_shard_count(client)


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


def create_tenants(client: weaviate.Client, class_name: str, tenants: List[Tenant], stage="stage_1"):
    client.schema.add_class_tenants(class_name=class_name, tenants=tenants)

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

def backend_provider():
    backend = os.environ.get("BACKUP_BACKEND_PROVIDER")
    if backend is None or backend == "":
        return "filesystem"
    return backend


def temp_backup_url_create():
    return f"http://localhost:{WEAVIATE_PORT}/v1/backups/{backend_provider()}"


def temp_backup_url_create_status(backup_name):
    return f"http://localhost:{WEAVIATE_PORT}/v1/backups/{backend_provider()}/{backup_name}"


def temp_backup_url_restore(backup_name):
    return f"http://localhost:{WEAVIATE_PORT}/v1/backups/{backend_provider()}/{backup_name}/restore"


def temp_backup_url_restore_status(backup_name):
    return f"http://localhost:{WEAVIATE_PORT}/v1/backups/{backend_provider()}/{backup_name}/restore"


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


def restore_backup(client: weaviate.Client, name):
    restore_body = {"id": name}
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


client = weaviate.Client(f"http://localhost:{WEAVIATE_PORT}")

backup_name = f"{int(datetime.datetime.now().timestamp())}_stage_1"

class_names = ["Class_A", "Class_B"]
num_tenants_hot = 1_000
num_tenants_cold = num_tenants_hot * 2

logger.info(f"Step 0, reset everything, import schema")
reset_schema(client, class_names)

logger.info(f"Step 1, create {num_tenants_hot} hot tenants and {num_tenants_cold} cold tenants per class")
tenants = [
    Tenant(name=f"{i}_tenant", activity_status=schema.TenantActivityStatus.HOT)
    for i in range(num_tenants_hot)
] + [
    Tenant(name=f"{i + num_tenants_hot}_tenant", activity_status=schema.TenantActivityStatus.COLD)
    for i in range(num_tenants_cold)
]
for class_name in class_names:
    create_tenants(
        client,
        class_name,
        tenants=tenants,
        stage="stage_1",
    )


logger.info("Step 2, create backup of current instance including all classes")
create_backup(client, backup_name)

logger.info("Step 3, delete all classes")
client.schema.delete_all()

logger.info("Step 4, restore backup mark")
restore_backup(client, backup_name)

logger.info("Step 10, run test and make sure results are same as on original instance at stage 1")
for class_name in class_names:
    
    actual_tenants = client.schema.get_class_tenants(class_name)
    logger.info(f"{class_name}: {len(actual_tenants)} tenants")
    assert len(actual_tenants) == len(tenants), f"Expected {tenants} tenants, but got {len(actual_tenants)}"

