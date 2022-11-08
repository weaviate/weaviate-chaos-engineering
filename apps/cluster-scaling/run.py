import weaviate
from loguru import logger
from typing import Optional
import numpy as np
import argparse

def reset_schema(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig":{
            "efConstruction": 32,
            "maxConnections": 4,
        },
        "class": "Example",
        "invertedIndexConfig":{
            "indexTimestamps":False,
        },
        "properties": [
            {
                "dataType": [ "int" ],
                "name": "position",
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

def load_records(client: weaviate.Client, max_records=100000):
    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        for i in range(max_records):
            if i % 1000 == 0:
                logger.info(f"Writing record {i}/{max_records}")
            batch.add_data_object(
                data_object={
                    "position": i,
                },
                vector=np.random.rand(32,1),
                class_name="Example"
            )

    logger.info(f"Finished writing {max_records} records")

def verify(client):
    where = {
            "path": ["position"],
            "operator": "Equal",
            "valueInt": 7,
            }
    query_result = (
      client.query
      .get("Example", "position")
      .with_where(where)
      .with_limit(100)
      .do()
    )

    pos = query_result['data']['Get']['Example'][0]['position']
    if pos != 7: 
        logger.warn(f"wrong pos: wanted 7, got {pos}")
        os.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-a', '--action', default='import')
    parser.add_argument('-p', '--port', default=8080)
    args = parser.parse_args()

    port = int(args.port)
    client = weaviate.Client(f"http://localhost:{port}", timeout_config=int(30))

    if args.action == "import":
        load_records(client, 100)
    elif args.action == "verify":
        verify(client)
    elif args.action == "schema":
        reset_schema(client)
    else:
        logger.error("unknown --action option")
