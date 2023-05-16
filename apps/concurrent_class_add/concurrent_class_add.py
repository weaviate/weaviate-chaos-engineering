import argparse
import random
import time

import weaviate
from weaviate import Config

parser = argparse.ArgumentParser()
parser.add_argument("number")
args = parser.parse_args()


def get_properties():
    return [
        {
            "indexInverted": False,
            "dataType": ["text"],
            "description": "Text property",
            "name": "text",
        },
        {
            "dataType": ["text"],
            "description": "Tokenized Text property",
            "name": "tokenized_text",
        },
        {
            "indexInverted": False,
            "dataType": ["string"],
            "description": "Document id",
            "name": "doc_id",
        },
        {
            "dataType": ["string"],
            "description": "Project id",
            "name": "project_id",
        },
        {
            "dataType": ["int"],
            "description": "The index of the Node",
            "name": "node_index",
        },
        {
            "dataType": ["string"],
            "description": "The ref_doc_id of the Node",
            "name": "ref_doc_id",
        },
        {
            "indexInverted": False,
            "dataType": ["string"],
            "description": "node_info (in JSON)",
            "name": "node_info",
        },
        {
            "indexInverted": False,
            "dataType": ["string"],
            "description": "doc_hash",
            "name": "doc_hash",
        },
        {
            "indexInverted": False,
            "dataType": ["string"],
            "description": "extra_info",
            "name": "extra_info",
        },
        {
            "indexInverted": False,
            "dataType": ["int"],
            "description": "index",
            "name": "index",
        },
    ]


class_name = "RandomClass" + str(args.number)

client = weaviate.Client(
    "http://localhost:8080",
    additional_config=Config(grpc_port_experimental=50051),
    startup_period=10,
)
try:
    client.schema.delete_class(class_name)
except:
    pass

props = get_properties()

start = time.time()
class_obj = {
    "class": class_name,
    "properties": props,
}
client.schema.create_class(class_obj)
print(f"add class {class_name} took {time.time() - start}s")

start = time.time()
with client.batch as batch:
    for i in range(10):
        batch.add_data_object(
            {
                "extra_info": "some random text",
                "index": random.randint(a=0, b=1000000),
                "doc_hash": "longHashThatDoesNotMatter",
                "node_info": "some text",
                "ref_doc_id": str(random.randint(a=0, b=1000000)),
                "node_index": random.randint(a=0, b=1000000),
                "project_id": str(random.randint(a=0, b=1000000)),
                "doc_id": str(random.randint(a=0, b=1000000)),
                "tokenized_text": str(random.randint(a=0, b=1000000)),
            },
            class_name=class_name,
        )
print(f"add objects {class_name} took {time.time() - start}s")

start = time.time()

for i in range(10):
    client.query.get(class_name, [p["name"] for p in props]).with_additional(
        ["id", "vector"]
    ).with_hybrid("1", alpha=0.0).with_limit(10).do()
print(f"query {class_name} took {time.time() - start}s")

start = time.time()
client.schema.delete_class(class_name)
print(f"delete {class_name} took {time.time() - start}s")
