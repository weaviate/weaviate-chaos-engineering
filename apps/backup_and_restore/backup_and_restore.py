import weaviate
import datetime
import time
from loguru import logger
from typing import Optional
import random
import numpy as np
import uuid
import sys

def reset_schema(client: weaviate.Client, class_names):
    client.schema.delete_all()
    for class_name in class_names:
        class_obj = {
            "vectorizer": "none",
            "vectorIndexConfig":{
                "efConstruction": 64,
                "maxConnections": 4,
                "cleanupIntervalSeconds": 10,
            },
            "class": class_name,
            "invertedIndexConfig":{
                "indexTimestamps":False,
            },
            "properties": [
                {
                    "dataType": [ "boolean" ],
                    "name": "should_be_deleted",
                },
                {
                    "dataType": [ "boolean" ],
                    "name": "is_divisible_by_four",
                },
                {
                    "dataType": [ "int" ],
                    "name": "index_id",
                },
                {
                    "dataType": [ "string" ],
                    "name": "stage",
                },
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

def load_records(client: weaviate.Client, class_name="Class", start=0, end=100_000, stage="stage_0"):

    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        delete_threshold = (end-start)*0.1 + start
        for i in range(start, end):
            if i % 10000 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{end}")
            data_object={
                "should_be_deleted": i<delete_threshold, # mark 10% of all records for future deletion
                "index_id": i, # same as UUID, this way we can retrieve both using the primary key and the inverted index and make sure the results match
                "stage": stage, # allows for setting filters that match the import stage
                "is_divisible_by_four": i%4 == 0, # an arbitrary field that matches 1/4 of the dataset to allow filtered searches later on
            }
            vector=np.random.rand(32,1)
            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {end-start} records")

def delete_records(client: weaviate.Client, class_name):
    client.batch.delete_objects(
        class_name=class_name,
        where={
            'operator': 'Equal',
            'path': ['should_be_deleted'],
            'valueBoolean': True
        },
        output='minimal',
        dry_run=False,
    )

def fatal(msg):
    logger.error(msg)
    sys.exit(1)

def success(msg):
    logger.success(msg)


def validate_stage(client: weaviate.Client, class_name, start=0, end=100_000, stage="stage_0", expected_count=0):
    # the filter removes 1/4 of elements
    expected_filtered_count = expected_count * 3 / 4

    result = client.query.aggregate(class_name) \
        .with_fields('meta { count }') \
        .with_fields("index_id {count}") \
        .do()

    logger.info("Aggregation without filters:")
    total_count = result['data']['Aggregate'][class_name][0]['meta']['count']
    prop_count = result['data']['Aggregate'][class_name][0]['index_id']['count']

    if total_count != expected_count:
        fatal(f"{class_name} {stage}: got {total_count} objects, wanted {expected_count}")
    else:
        success(f"{class_name} {stage}: got {total_count} objects, wanted {expected_count}")

    if prop_count != expected_count:
        fatal(f"{class_name} {stage}: got {prop_count} props, wanted {expected_count}")
    else:
        success(f"{class_name} {stage}: got {prop_count} props, wanted {expected_count}")

    logger.info("Aggregation with filters")
    result = client.query.aggregate(class_name) \
        .with_where({'operator': 'Equal', 'valueBoolean':False, 'path':["is_divisible_by_four"]}) \
        .with_fields('meta { count }') \
        .with_fields("index_id {count}") \
        .do()

    total_count = result['data']['Aggregate'][class_name][0]['meta']['count']
    prop_count = result['data']['Aggregate'][class_name][0]['index_id']['count']

    if total_count != expected_filtered_count:
        fatal(f"{class_name} {stage}: got {total_count} objects, wanted {expected_filtered_count}")
    else:
        success(f"{class_name} {stage}: got {total_count} objects, wanted {expected_filtered_count}")

    if prop_count != expected_filtered_count:
        fatal(f"{class_name} {stage}: got {prop_count} props, wanted {expected_count}")
    else:
        success(f"{class_name} {stage}: got {prop_count} props, wanted {expected_count}")



client = weaviate.Client("http://localhost:8080")

class_names=['Class_A', 'Class_B']
objects_per_stage = 100_000
start_stage_1 = 0
end_stage_1 = objects_per_stage
expected_count_stage_1 = 0.9 * end_stage_1 # because of 10% deletions
start_stage_2 = end_stage_1
end_stage_2 = start_stage_1 + objects_per_stage
expected_count_stage_2 = 0.9 * end_stage_2 # because of 10% deletions

logger.info(f"Step 0, reset everything, import schema")
reset_schema(client, class_names)

logger.info(f"Step 1, import first half of objects across {len(class_names)} classes")
for class_name in class_names:
    load_records(client, class_name, start=start_stage_1, end=end_stage_1, stage="stage_1")

logger.info("delete 10% of objects to make sure deletes are covered")
for class_name in class_names:
    delete_records(client, class_name)

logger.info("Step 3, run control test on original instance validating all assumptions at stage 1")
for class_name in class_names:
    validate_stage(client, class_name, start=start_stage_1, end=end_stage_1, stage="stage_1", expected_count=expected_count_stage_1)
# Step 4, create backup of current instance including all classes
# Step 5, import second half of objects
# Step 6, delete 10% of objects of new import
# Step 7, run control test on original instance validating all assumptions at stage 2
# Step 8, delete all classes
# Step 9, restore backup at half-way mark
# Step 10, run test and make sure results are same as on original instance at stage 1
# Step 11, import second half of objects
# Step 12, delete 10% of objects of new import
# Step 13, run test and make sure results are same as on original instance at stage 2

