import json
import random
from time import time
from typing import Sequence
import torch
import weaviate
import math
from uuid import uuid4

BATCH_SIZE = 256

client = weaviate.Client(
    url="http://localhost:8080",
    timeout_config=(20, 120),
)  # or another location where your Weaviate instance is running


schema = {
    "classes": [
        {
            "class": "SemanticUnit",
            "description": "A written text, for example a news article or blog post",
            "vectorIndexType": "hnsw",
            "vectorIndexConfig": {
                "efConstruction": 128,
                "maxConnections": 64,
            },
            # "shardingConfig": {
            #     "desiredCount":4,
            # },
            "vectorizer": "none",
            "properties": [
                {"dataType": ["string"], "description": "ID", "name": "reference"},
                {
                    "dataType": ["text"],
                    "description": "titles of the unit",
                    "name": "title",
                },
                {"dataType": ["text"], "description": "semantic unit flat text", "name": "text"},
                {"dataType": ["string"], "description": "document type", "name": "docType"},
                {
                    "dataType": ["int"],
                    "description": "so we can do some int queries",
                    "name": "itemId",
                },
                {
                    "dataType": ["int"],
                    "description": "so we can do some int queries",
                    "name": "itemIdHundred",
                },
                {
                    "dataType": ["int"],
                    "description": "so we can do some int queries",
                    "name": "itemIdTen",
                },
                {
                    "dataType": ["int"],
                    "description": "so we can do some int queries",
                    "name": "dummy",
                },
            ],
        }
    ]
}
# cleanup from previous runs
client.schema.delete_all()
client.schema.create(schema)


client.batch.configure(
    batch_size=BATCH_SIZE,
)


data = []
with open("data.json", "r") as f:
    data = json.load(f)

update_ratio = 0.0

# ids=[]

# if update_ratio != 0:
#     id_ratio = 1-update_ratio
#     id_count = len(data) * id_ratio
#     for i in range(int(id_count)):
#         ids+=[str(uuid4())]

# def get_uuid():
#     if update_ratio == 0:
#         return None

#     return random.choice(ids)


def normalize(vector: Sequence):
    norm: int = 0
    for x in vector:
        norm += x * x
    norm = math.sqrt(norm)
    for i, x in enumerate(vector):
        vector[i] = x / norm
    return vector


start = time()
with client.batch as batch:
    for i, doc in enumerate(data):
        props = {
            "title": doc["properties"]["title"],
            "text": doc["properties"]["text"],
            "docType": doc["properties"]["token"],
            "itemId": doc["properties"]["itemId"],
            "itemIdHundred": doc["properties"]["itemIdHundred"],
            "itemIdTen": doc["properties"]["itemIdTen"],
            "dummy": 7,
        }
        batch.add_data_object(
            data_object=props,
            class_name="SemanticUnit",
            uuid=doc["id"],
            vector=normalize(doc["vector"]),
        )
