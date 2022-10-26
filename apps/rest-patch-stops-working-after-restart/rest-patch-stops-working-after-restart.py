"""
https://semi-technology.atlassian.net/browse/WEAVIATE-151
"""
import sys
from typing import Dict
import weaviate
from loguru import logger


def create_weaviate_schema(client: weaviate.Client) -> None:
    schema_class = {
        "classes": [{
            "class": "PatchStopsWorkingAfterRestart",
            "properties": [
                {
                    "dataType": ["string"],
                    "name": "description",
                    "tokenization": "word",
                    "indexInverted": True
                },
            ]}],
        "vectorizer": "none",
        "vectorIndexType": "hnsw",
        "invertedIndexConfig": {
            "bm25": {
                "b": 0.75,
                "k1": 1.2
            },
            "cleanupIntervalSeconds": 60,
            "stopwords": {
                "preset": "en"
            }
        }
    }
    # add schema
    if not client.schema.contains(schema_class):
        client.schema.create(schema_class)


def get_body(index: str, req_type: str) -> Dict[str, str]:
    return {
        "description": f"this is an update number: {index} with {req_type}",
    }


def create_object_if_it_does_not_exist(client: weaviate.Client, class_name: str, object_id: str) -> None:
    try:
        if not client.data_object.exists(object_id, class_name):
            client.data_object.create(get_body(0, "create"), class_name, object_id)
    except Exception:
        logger.exception(f"Error adding {class_name} object - id: {object_id}")


def constant_updates(client: weaviate.Client, class_name: str, object_id: str) -> None:
    loops = 1000
    for i in range(loops):
        try:
            client.data_object.replace(get_body(i, "put"), class_name, object_id)
            client.data_object.update(get_body(i, "patch"), class_name, object_id, [0.1, 0.2, 0.1, 0.3])
        except Exception:
            logger.exception(f"Error updating {class_name} object - id: {object_id}")
            raise


if __name__ == "__main__":
    client = weaviate.Client("http://localhost:8080")
    try:
        create_weaviate_schema(client)
        class_name = "PatchStopsWorkingAfterRestart"
        object_id = "07e9828d-ff0a-5e47-8101-b52312345678"
        create_object_if_it_does_not_exist(client, class_name, object_id)
        constant_updates(client, class_name, object_id)
    except:
        logger.exception("An error ocurred!")
        sys.exit(1)
