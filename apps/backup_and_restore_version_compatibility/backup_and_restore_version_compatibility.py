import datetime
import numpy as np
import requests
import sys
import time
import weaviate
import uuid

from loguru import logger
from typing import Optional


num_objects = 1000

def reset_schema(client: weaviate.Client, class_names):
    client.schema.delete_all()
    for class_name in class_names:
        class_obj = {
            "vectorizer": "none",
            "vectorIndexConfig":{
                "efConstruction": 128,
                "maxConnections": 16,
                "ef": 256,
                "cleanupIntervalSeconds": 10,
            },
            "class": class_name,
            "invertedIndexConfig":{
                "indexTimestamps":False,
            },
            "properties": [
                {
                    "dataType": ["string"],
                    "name": "name"
                },
                {
                    "dataType": ["int"],
                    "name": "index"
                }
            ]
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
                'result' in result
                and 'errors' in result['result']
                and 'error' in result['result']['errors']
            ):
                for message in result['result']['errors']['error']:
                    logger.error(message['message'])

def success(msg):
    logger.success(msg)

def fatal(msg):
    logger.error(msg)
    sys.exit(1)

def load_records(client: weaviate.Client, class_name="Class"):
    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(num_objects):
            if i % 100 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{num_objects}")
            data_object={
                "name": f"object#{i}",
                "index": i
            }
            vector=np.random.rand(32,1)
            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {num_objects} records")

def validate_records(client: weaviate.Client, class_name: str):
    res = client.query.get(class_name=class_name, properties=['name', 'index']).do()['data']['Get'][class_name]
    sorted_res = sorted(res, key=lambda d: d['index'])
    for idx, item in enumerate(sorted_res):
        if item['index'] != idx:
            fatal(f"'{class_name}' was not properly restored. expected {num_objects} results, received {len(res)}")
    success(f"Restore for '{class_name}' has been validated")

def hostname(client: weaviate.Client):
    host = client._connection.url
    return host

def backup_url_create(client: weaviate.Client):
    return f"{hostname(client)}/v1/backups/s3"

def backup_url_create_status(client: weaviate.Client, backup_name):
    return f"{hostname(client)}/v1/backups/s3/{backup_name}"

def backup_url_restore(client: weaviate.Client, backup_name):
    return f"{hostname(client)}/v1/backups/s3/{backup_name}/restore"

def backup_url_restore_status(client: weaviate.Client, backup_name):
    return f"{hostname(client)}/v1/backups/s3/{backup_name}/restore"

def create_backup(client: weaviate.Client, name):
    create_body = {'id': name }
    res = requests.post(backup_url_create(client), json = create_body)
    if res.status_code > 399:
        fatal(f"Backup Create returned status code {res.status_code} with body: {res.json()}")
    while True:
        time.sleep(1)
        res = requests.get(backup_url_create_status(client, name))
        res_json = res.json()
        if res_json['status'] == 'SUCCESS':
            success(f"Backup creation successful")
            break
        if res_json['status'] == 'FAILED':
            fatal(f"Backup failed with res: {res_json}")

def restore_backup(client: weaviate.Client, name):
    restore_body = {'id': name }
    res = requests.post(backup_url_restore(client, name), json = restore_body)
    if res.status_code > 399:
        fatal(f"Backup Restore returned status code {res.status_code} with body: {res.json()}")
    while True:
        time.sleep(1)
        res = requests.get(backup_url_restore_status(client, name))
        res_json = res.json()
        if res_json['status'] == 'SUCCESS':
            success(f"Restore succeeded")
            break
        if res_json['status'] == 'FAILED':
            fatal(f"Restore failed with res: {res_json}")

node1_client = weaviate.Client("http://localhost:8080")

class_names=['Class_A', 'Class_B']
reset_schema(node1_client, class_names)

logger.info("Inserting data...")
for class_name in class_names:
    load_records(node1_client, class_name)

logger.info("Creating backup...")
backup_name = f"backup_{int(datetime.datetime.now().timestamp())}"
create_backup(node1_client, backup_name)

node2_client = weaviate.Client("http://localhost:8081")
logger.info("Restoring backup...")
restore_backup(node2_client, backup_name)

logger.info("Validating restored data...")
for class_name in class_names:
    validate_records(node2_client, class_name)

success("Test complete!")
