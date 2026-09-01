import random
import argparse
from loguru import logger
from typing import Optional
import uuid
import weaviate
import h5py

def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig":{
        },
        "class": "Set",
        "invertedIndexConfig":{
            "indexTimestamps":False,
        },
        "properties": [
            {
                "dataType": [ "boolean" ],
                "name": "prop_1",
            },
            {
                "dataType": [ "boolean" ],
                "name": "prop_2",
            },
            {
                "dataType": [ "int" ],
                "name": "modulo_11",
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

def load_records(client: weaviate.Client, vectors):
    client.batch.configure(batch_size=100, callback=handle_errors)
    i = 0
    if vectors == None:
        vectors = [None]*10_000_000

    with client.batch as batch:
        for vector in vectors:
            if i % 10000 == 0:
                logger.info(f"writing record {i}/{len(vectors)}")
            data_object={
                "prop_1": True,
                "prop_2": i % 11 < 10,
                "modulo_11": i%11,
            }

            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name="Set",
                uuid=uuid.UUID(int=i),
            )
            i+=1
    logger.info(f"Finished writing {len(vectors)} records")

parser = argparse.ArgumentParser()
client = weaviate.Client("http://localhost:8080")

parser.add_argument('-v', '--with-vectors')
args = parser.parse_args()


vectors = None
reset_schema(client)
if (args.with_vectors) != None:
    f = h5py.File(args.with_vectors)
    vectors = f["train"]


load_records(client, vectors)

