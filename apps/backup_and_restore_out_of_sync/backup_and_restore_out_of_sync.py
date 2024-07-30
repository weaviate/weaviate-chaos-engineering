import datetime
import os
import requests
import sys
import time
import weaviate

from loguru import logger


def create_class(client: weaviate.Client, class_name):
    class_obj = {
        "class": class_name,
        "vectorizer": "none",
        "invertedIndexConfig": {"bm25": {"b": 0.75, "k1": 1.2}},
        "replicationConfig": {"factor": 1},
        "vectorIndexType": "flat",
        "shardingConfig": {
            "desiredCount": 1,
        },
        "vectorIndexConfig": {
            "bq": {
                "enabled": True,
                "cache": True,
            },
        },
        "properties": [
            {
                "name": "title",
                "description": "TITLE",
                "dataType": ["text"],
                "indexSearchable": True,
                "indexSearchable": True,
                "indexFilterable": True,
            }
        ],
    }
    client.schema.create_class(class_obj)


def reset_schema(client1: weaviate.Client, client2: weaviate.Client):
    client1.schema.delete_all()

    for i in range(0, 50):
        create_class(client1, f"Books1_{i}")

    for i in range(50, 100):
        create_class(client2, f"Books1_{i}")


def query_collections(client1: weaviate.Client, client2: weaviate.Client):

    logger.info(f"Length of collection from node 1: {len(client1.schema.get()['classes'])}")
    logger.info(f"Length of collection from node 2: {len(client2.schema.get()['classes'])}")
    for i in range(0, 100):
        collection1 = client1.schema.get(f"Books1_{i}")
        collection2 = client2.schema.get(f"Books1_{i}")

        if collection1["class"] != collection2["class"]:
            fatal(f"Collection name is not correct")


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
    return f"http://localhost:8081/v1/backups/{backend_provider()}"


def temp_backup_url_create_status(backup_name):
    return f"http://localhost:8080/v1/backups/{backend_provider()}/{backup_name}"


def temp_backup_url_restore(backup_name):
    return f"http://localhost:8081/v1/backups/{backend_provider()}/{backup_name}/restore"


def temp_backup_url_restore_status(backup_name):
    return f"http://localhost:8080/v1/backups/{backend_provider()}/{backup_name}/restore"


def create_backup(client: weaviate.WeaviateClient, name):
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


def restore_backup(client: weaviate.WeaviateClient, name):
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


client1 = weaviate.Client(url="http://localhost:8080")
client2 = weaviate.Client(url="http://localhost:8081")

backup_name = f"{int(datetime.datetime.now().timestamp())}_stage_1"

logger.info(f"Step 0, reset everything, import schema")
reset_schema(client1, client2)

logger.info(f"Step 1, query each class from both nodes")
query_collections(client1, client2)

logger.info("Step 2, create backup using node 2")
create_backup(client1, backup_name)

logger.info("Step 3, delete the schema and all objects")
client1.schema.delete_all()

logger.info("Step 4, restore backup using node 2")
restore_backup(client1, backup_name)

logger.info(f"Step 5, query each class from both nodes")
query_collections(client1, client2)
