from loguru import logger
import uuid
import weaviate
import weaviate.classes as wvc

class_name = "Vector"


def reset_schema(client: weaviate.WeaviateClient, efC, m, shards, distance):
    client.collections.delete_all()
    client.collections.create(
        name=class_name,
        vectorizer_config=wvc.Configure.Vectorizer.none(),
        vector_index_config=wvc.Configure.vector_index(
            ef_construction=efC,
            max_connections=m,
            ef=-1,
            distance_metric=wvc.VectorDistance(distance),
        ),
        properties=[
            wvc.Property(
                name="i",
                data_type=wvc.DataType.INT,
            )
        ],
        inverted_index_config=wvc.Configure.inverted_index(index_timestamps=False),
        sharding_config=wvc.Configure.sharding(desired_count=shards),
    )


def load_records(
    client: weaviate.WeaviateClient, vectors, compression, override, num_workers, dynamic
):
    if vectors == None:
        vectors = [None] * 10_000_000
    client.batch.configure(dynamic=dynamic, batch_size=1000, num_workers=num_workers)
    with client.batch as batch:
        for i, vector in enumerate(vectors):
            if i % 10000 == 0:
                logger.info(f"writing record {i}/{len(vectors)}")
            batch.add_object(
                properties={"i": i},
                vector=vector,
                collection=class_name,
                uuid=uuid.UUID(int=i),
            )

    for err in batch.failed_objects():
        logger.error(err.message)

    logger.info(f"Finished writing {len(vectors)} records")
