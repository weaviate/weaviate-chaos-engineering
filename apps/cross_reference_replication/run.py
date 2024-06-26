import argparse
import sys
from typing import List, Union
import uuid
from loguru import logger
import random

from wonderwords import RandomSentence

import weaviate
import weaviate.classes as wvc
from weaviate.classes.config import Property, DataType, ReferenceProperty, Configure, VectorDistances
from weaviate.classes.query import Filter, QueryReference

s = RandomSentence()

# Initialize the global singleton
cfg = None
client = None

total_paragraphs = 1_000_000
paragraph_to_pages_ratio = 5
pages_to_documents_ratio = 10

objects_per_cycle = 10_000

replication_factor = 3
sharding_factor = 3

report_ratio = 1000

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
    pages = client.collections.get("Page")
    documents = client.collections.get("Document")
    return documents, pages, paragraphs

def reset_schema() -> Union[weaviate.collections.Collection, weaviate.collections.Collection, weaviate.collections.Collection]:
    document = client.collections.create(
        name="Document",
        description="A document with pages",
        properties=[
            Property(name="title", data_type=DataType.TEXT),
            Property(name="description", data_type=DataType.TEXT),
            Property(name="category", data_type=DataType.TEXT),
            Property(name="random_number", data_type=DataType.INT),
        ],
        vectorizer_config=wvc.config.Configure.Vectorizer.none(),
        replication_config=Configure.replication(
            factor=replication_factor
        ),
        sharding_config=Configure.sharding(
            desired_count=sharding_factor
        ),
    )

    page = client.collections.create(
        name="Page",
        description="A page of text",
        properties=[
            Property(name="category", data_type=DataType.TEXT),
            Property(name="number", data_type=DataType.NUMBER),
            Property(name="random_number", data_type=DataType.INT),
        ],
        vectorizer_config=wvc.config.Configure.Vectorizer.none(),
        references=[
            ReferenceProperty(
                name="document",
                target_collection="Document",
            )
        ],
        replication_config=Configure.replication(
            factor=replication_factor
        ),
        sharding_config=Configure.sharding(
            desired_count=sharding_factor
        ),
    )

    paragraph = client.collections.create(
        name="Paragraph",
        description="A paragraph of a document",
        properties=[
            Property(name="text", data_type=DataType.TEXT),
            Property(name="random_number", data_type=DataType.INT),
        ],
        vectorizer_config=wvc.config.Configure.Vectorizer.none(),
        references=[
            ReferenceProperty(
                name="page",
                target_collection="Page",
            ),
            ReferenceProperty(
                name="document",
                target_collection="Document",
            )
        ],
        vector_index_config=Configure.VectorIndex.flat(
            distance_metric=VectorDistances.COSINE,                     # Distance metric
            vector_cache_max_objects=1000000,                           # Maximum number of objects in the cache
        ),
        replication_config=Configure.replication(
            factor=replication_factor
        ),
        sharding_config=Configure.sharding(
            desired_count=sharding_factor
        ),
    )
    return document, page, paragraph

def delete_data():
    client.collections.delete_all()

def import_data(document, page, paragraph):
    with document.batch.dynamic() as batch:
        for i in range(total_paragraphs//pages_to_documents_ratio//paragraph_to_pages_ratio):
            batch.add_object(
                uuid=uuid_from_int(i),
                properties={
                    "title": random.choice(sentences),
                    "description": random.choice(sentences),
                    "category": random.choice(categories),
                    "random_number": random.randint(0, 1000),
                    "number": i,
                }
            )

            if i % report_ratio == 0:
                logger.info(f"document {i}")


    with page.batch.dynamic() as batch:
        for i in range(total_paragraphs//paragraph_to_pages_ratio):
            document = uuid_from_int(i//pages_to_documents_ratio)
            batch.add_object(
                uuid=uuid_from_int(i),
                properties={
                    "category": random.choice(categories),
                    "random_number": random.randint(0, 1000),
                    "number": i,
                },
                references={
                    "document": document,
                },
            )

            if i % report_ratio == 0:
                logger.info(f"page {i}")


    with paragraph.batch.dynamic() as batch:
        for i in range(total_paragraphs):
            page = uuid_from_int(i//paragraph_to_pages_ratio)
            document = uuid_from_int(i//pages_to_documents_ratio//paragraph_to_pages_ratio)
            batch.add_object(
                uuid=uuid_from_int(i),
                properties={
                    "text": random.choice(sentences),
                    "random_number": random.randint(0, 1000),
                },
                vector=generate_random_vector(),
                references={
                    "document": document,
                    "page": page,
                },
            )
            if i % report_ratio == 0:
                logger.info(f"paragraph {i}")

def validate_data(document, page, paragraph):
    # Validate the data
    logger.info(f"{len(document)} documents, {len(page)} pages, {len(paragraph)} paragraphs")

def query_data():
    document, page, paragraph = get_collections()
    response = paragraph.query.near_vector(
        near_vector=generate_random_vector(),
        filters=Filter.by_ref(link_on="document").by_property("number").equal(1),
        return_references=QueryReference(link_on="document", return_properties=["category"]),
        limit=100,
    )

    return response

def run():
    delete_data()
    document, page, paragraph = reset_schema()
    import_data(document, page, paragraph)
    validate_data(document, page, paragraph)
    query_data()


if __name__ == "__main__":

    configure_logger()
    cfg = parse_arguments()
    client = weaviate.connect_to_local(host=cfg.host)
    run()
