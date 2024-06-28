import argparse
import sys
import time
from typing import List, Union
import uuid
from loguru import logger
import random

import requests
from wonderwords import RandomSentence

import weaviate
import weaviate.classes as wvc
from weaviate.classes.config import Property, DataType, ReferenceProperty, Configure
from weaviate.classes.query import Filter, QueryReference
s = RandomSentence()

# Initialize the global singleton
cfg = None
client = None

total_paragraphs = 1_000_000
paragraph_to_document_ratio = 3
filter_count = 40

number_range = 1000

objects_per_cycle = 10_000

replication_factor = 3
sharding_factor = 3

report_ratio = 1000
use_vectors = False
filter_cross_reference = True
get_cross_reference = True

# the wonderwords library is fairly slow to create sentences. Therefore we're
# not creating them on the fly. Let's pre-create some sentences and then pick
# random combinations of sentences at import time
sentences = [s.sentence() for i in range(10000)]
categories = [s.sentence().split(" ")[-1] for i in range(50)]

def configure_logger():
    logger.remove()  # Remove the default logger
    logger.add(
        sys.stderr,
        format="<green>{elapsed}</green> | <level>{level: <8}</level> | <level> {message} </level>",
    )

class Config:
    def __init__(self, skip_schema, host, replication_factor):
        self.skip_schema = skip_schema
        self.host = host
        self.replication_factor = replication_factor



def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Kill Weaviate during batch deletes and validate BM25 still works afterwards"
    )
    parser.add_argument("--skip-schema", action="store_true", help="Skip processing schema if set")
    parser.add_argument("--host", default="localhost", help="Specify the host (default: localhost)")
    parser.add_argument(
        "--replication-factor",
        type=int,
        default=1,
        help="Specify the replication factor (default: 1)",
    )

    args = parser.parse_args()
    return Config(
        skip_schema=args.skip_schema,
        host=args.host,
        replication_factor=args.replication_factor
    )



def generate_random_vector(size: int = 128) -> List[float]:
    return [random.random() for _ in range(size)]

def uuid_from_int(i: int) -> str:
    return str(uuid.UUID(int=i))

def get_collections() -> Union[weaviate.collections.Collection, weaviate.collections.Collection, weaviate.collections.Collection]:
    paragraphs = client.collections.get("Paragraph")
    documents1 = client.collections.get("Document1")
    documents2 = client.collections.get("Document2")
    return documents1, documents2, paragraphs

def reset_schema() -> Union[weaviate.collections.Collection, weaviate.collections.Collection, weaviate.collections.Collection]:
    document1 = client.collections.create(
        name="Document1",
        description="A document with pages",
        properties=[
            Property(name="title", data_type=DataType.TEXT),
            Property(name="description", data_type=DataType.TEXT),
            Property(name="category", data_type=DataType.TEXT),
            Property(name="number", data_type=DataType.INT),
        ],
        vectorizer_config=wvc.config.Configure.Vectorizer.none(),
        replication_config=Configure.replication(
            factor=replication_factor
        ),
        sharding_config=Configure.sharding(
            desired_count=sharding_factor
        ),
    )

    document2 = client.collections.create(
        name="Document2",
        description="A document with pages",
        properties=[
            Property(name="title", data_type=DataType.TEXT),
            Property(name="description", data_type=DataType.TEXT),
            Property(name="category", data_type=DataType.TEXT),
            Property(name="number", data_type=DataType.INT),
        ],
        vectorizer_config=wvc.config.Configure.Vectorizer.none(),
        replication_config=Configure.replication(
            factor=replication_factor
        ),
        sharding_config=Configure.sharding(
            desired_count=sharding_factor
        )
    )

    paragraph = client.collections.create(
        name="Paragraph",
        description="A paragraph of a document",
        properties=[
            Property(name="text", data_type=DataType.TEXT),
            Property(name="number", data_type=DataType.INT),
        ],
        vectorizer_config=wvc.config.Configure.Vectorizer.none(),
        references=[
            ReferenceProperty(
                name="document2",
                target_collection="Document2",
            )
        ],
        replication_config=Configure.replication(
            factor=replication_factor
        ),
        sharding_config=Configure.sharding(
            desired_count=sharding_factor
        ),
    )
    return document1, document2, paragraph

def delete_data():
    client.collections.delete_all()

def import_data():   
    document1, document2, paragraph = get_collections() 
    with document1.batch.dynamic() as batch:
        for i in range(total_paragraphs//paragraph_to_document_ratio):
            other_document_2 = uuid_from_int(random.randint(0, total_paragraphs//paragraph_to_document_ratio))
            batch.add_object(
                uuid=uuid_from_int(i),
                properties={
                    "title": random.choice(sentences),
                    "description": random.choice(sentences),
                    "category": random.choice(categories),
                    "number": i,
                },
            )
            if i % report_ratio == 0:
                logger.info(f"document {i}")
            

    with document2.batch.dynamic() as batch:
        for i in range(total_paragraphs//paragraph_to_document_ratio):
            other_document_1 = uuid_from_int(random.randint(0, total_paragraphs//paragraph_to_document_ratio))
            batch.add_object(
                uuid=uuid_from_int(i),
                properties={
                    "title": random.choice(sentences),
                    "description": random.choice(sentences),
                    "category": random.choice(categories),
                    "number": random.randint(0, number_range),
                }
            )
            if i % report_ratio == 0:
                logger.info(f"document {i}")


    with paragraph.batch.dynamic() as batch:
        for i in range(total_paragraphs):
            #page_id = uuid_from_int(i//paragraph_to_pages_ratio)
            document_id = uuid_from_int(i//paragraph_to_document_ratio)
            object = {
                "uuid": uuid_from_int(i),
                "properties": {
                    "text": random.choice(sentences),
                    "number": random.randint(0, number_range),
                },
                "references": {
                    "document2": document_id,
                    #"page": page_id,
                }
            }
            if use_vectors:
                object["vector"] = generate_random_vector()
            batch.add_object(
                **object                
            )
            if i % report_ratio == 0:
                logger.info(f"paragraph {i}")

def validate_data():
    # Validate the data
    document1, document2, paragraph = get_collections()
    logger.info(f"{len(document1)} documents1, {len(document2)} document2, {len(paragraph)} paragraphs")

def query_data():
    _, _, paragraph = get_collections()
    random.seed(42)
    for i in range(100):
        doc_id_range = [i for i in range(number_range)]
        random.shuffle(doc_id_range)
        start_time = time.time()
        filters = None
        if filter_cross_reference:
            filters = (
                Filter.by_ref(link_on="document2").by_property("number").contains_any(doc_id_range[:filter_count])
            )
        return_references = None
        if get_cross_reference:
            return_references = QueryReference(link_on="document2", return_properties=["category"])
        if use_vectors:
            response = paragraph.query.near_vector(
                near_vector=generate_random_vector(),
                filters=filters,
                return_references=return_references,
                limit=400,
            )
        else:
            response = paragraph.query.bm25(
                query="word_that_does_not_exist",
                filters=filters,
                return_references=return_references,
                limit=400,
            )
        end_time = time.time()
        logger.info(f"Query took {(end_time - start_time)*1000:.2f}")
    return response

def get_stats():
    time.sleep(10)
    timestamp = int(time.time())
    for port in [2112, 2113, 2114]:
        url = f"http://localhost:{port}/metrics"
        logger.info(f"Getting stats from {url}")
        data = requests.get(url)
        with open(f"metrics_{port}_{timestamp}_{replication_factor}_{sharding_factor}.txt", "w") as f:
            f.write(data.text)



def run():
    #delete_data()
    #reset_schema()
    #import_data()
    #validate_data()
    query_data()
    get_stats()


if __name__ == "__main__":

    configure_logger()
    cfg = parse_arguments()
    client = weaviate.connect_to_local(host=cfg.host)
    run()
