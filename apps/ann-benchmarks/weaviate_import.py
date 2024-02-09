from loguru import logger
import uuid
import weaviate
import weaviate.classes.config as wvc
import time
from datetime import datetime
from typing import Dict
from weaviate.collections.classes.types import WeaviateField

class_name = "Vector"


def reset_schema(client: weaviate.WeaviateClient, efC, m, shards, distance):
    client.collections.delete_all()
    client.collections.create(
        name=class_name,
        vectorizer_config=wvc.Configure.Vectorizer.text2vec_contextionary(),
        vector_index_config=wvc.Configure.VectorIndex.hnsw(
            ef_construction=efC,
            max_connections=m,
            ef=-1,
            distance_metric=wvc.VectorDistances(distance),
        ),
        properties=[
            wvc.Property(
                name="int",
                data_type=wvc.DataType.INT,
            ),
            wvc.Property(
                name="number",
                data_type=wvc.DataType.NUMBER,
            ),
            wvc.Property(
                name="text",
                data_type=wvc.DataType.TEXT,
            ),
            wvc.Property(
                name="boolean",
                data_type=wvc.DataType.BOOL,
            ),
            wvc.Property(
                name="uuid",
                data_type=wvc.DataType.UUID,
            ),
            wvc.Property(
                name="date",
                data_type=wvc.DataType.DATE,
            ),
            wvc.Property(
                name="ints",
                data_type=wvc.DataType.INT_ARRAY,
            ),
            wvc.Property(
                name="numbers",
                data_type=wvc.DataType.NUMBER_ARRAY,
            ),
            wvc.Property(
                name="texts",
                data_type=wvc.DataType.TEXT_ARRAY,
            ),
            wvc.Property(
                name="booleans",
                data_type=wvc.DataType.BOOL_ARRAY,
            ),
            wvc.Property(
                name="uuids",
                data_type=wvc.DataType.UUID_ARRAY,
            ),
            wvc.Property(
                name="dates",
                data_type=wvc.DataType.DATE_ARRAY,
            ),
            wvc.Property(
                name="phone",
                data_type=wvc.DataType.PHONE_NUMBER,
            ),
            wvc.Property(
                name="geo",
                data_type=wvc.DataType.GEO_COORDINATES,
            ),
            wvc.Property(
                name="object",
                data_type=wvc.DataType.OBJECT,
                nested_properties=[
                    wvc.Property(
                        name="n_int",
                        data_type=wvc.DataType.INT,
                    ),
                    wvc.Property(
                        name="n_number",
                        data_type=wvc.DataType.NUMBER,
                    ),
                    wvc.Property(
                        name="n_text",
                        data_type=wvc.DataType.TEXT,
                    ),
                    wvc.Property(
                        name="n_boolean",
                        data_type=wvc.DataType.BOOL,
                    ),
                    wvc.Property(
                        name="n_uuid",
                        data_type=wvc.DataType.UUID,
                    ),
                    wvc.Property(
                        name="n_date",
                        data_type=wvc.DataType.DATE,
                    ),
                    wvc.Property(
                        name="n_object",
                        data_type=wvc.DataType.OBJECT,
                        nested_properties=[
                            wvc.Property(
                                name="nn_int",
                                data_type=wvc.DataType.INT,
                            ),
                        ],
                    ),
                ],
            ),
            wvc.Property(
                name="objects",
                data_type=wvc.DataType.OBJECT_ARRAY,
                nested_properties=[
                    wvc.Property(
                        name="n_ints",
                        data_type=wvc.DataType.INT_ARRAY,
                    ),
                    wvc.Property(
                        name="n_numbers",
                        data_type=wvc.DataType.NUMBER_ARRAY,
                    ),
                    wvc.Property(
                        name="n_texts",
                        data_type=wvc.DataType.TEXT_ARRAY,
                    ),
                    wvc.Property(
                        name="n_booleans",
                        data_type=wvc.DataType.BOOL_ARRAY,
                    ),
                    wvc.Property(
                        name="n_uuids",
                        data_type=wvc.DataType.UUID_ARRAY,
                    ),
                    wvc.Property(
                        name="n_dates",
                        data_type=wvc.DataType.DATE_ARRAY,
                    ),
                    wvc.Property(
                        name="n_objects",
                        data_type=wvc.DataType.OBJECT_ARRAY,
                        nested_properties=[
                            wvc.Property(
                                name="nn_ints",
                                data_type=wvc.DataType.INT_ARRAY,
                            ),
                        ],
                    ),
                ],
            ),
        ],
        inverted_index_config=wvc.Configure.inverted_index(index_timestamps=False),
        sharding_config=wvc.Configure.sharding(desired_count=shards),
    )


def load_records(
    client: weaviate.WeaviateClient,
    vectors,
    compression,
    dim_to_seg_ratio,
    override,
    max_records,
    alt_data_object,
):
    collection = client.collections.get(class_name)
    i = 0
    if vectors == None:
        vectors = [None] * 10_000_000
    if max_records > 0 and len(vectors) > max_records:
        vectors = vectors[:max_records]

    batch_size = 1000
    len_objects = len(vectors)

    with client.batch.fixed_size(batch_size=batch_size) as batch:
        for vector in vectors:
            if i == 100000 and compression == True and override == False:
                logger.info(f"pausing import to enable compression")
                break

            if i % 10000 == 0:
                logger.info(f"writing record {i}/{len_objects}")

            base = i
            if alt_data_object:
                base = i + 1

            batch.add_object(
                properties=create_data_object(base, i),
                # vector=vector,
                collection=class_name,
                uuid=uuid.UUID(int=i),
            )
            i += 1

    for err in client.batch.failed_objects:
        logger.error(err.message)

    if compression == True and override == False:
        collection.config.update(
            vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(
                quantizer=wvc.Reconfigure.VectorIndex.Quantizer.pq(
                    segments=int(len(vectors[0]) / dim_to_seg_ratio),
                )
            )
        )

        wait_for_all_shards_ready(collection)

        i = 100000
        with client.batch.fixed_size(batch_size=batch_size) as batch:
            while i < len_objects:
                vector = vectors[i]
                if i % 10000 == 0:
                    logger.info(f"writing record {i}/{len_objects}")

                batch.add_object(
                    properties=create_data_object(i, i),
                    # vector=vector,
                    collection=class_name,
                    uuid=uuid.UUID(int=i),
                )
                i += 1

        for err in client.batch.failed_objects:
            logger.error(err.message)

    logger.info(f"Finished writing {len_objects} records")


def create_data_object(base: int, baseGeo: int) -> Dict[str, WeaviateField]:
    return {
        "int": base,
        "number": base + 0.5,
        "text": f"text{base}",
        "boolean": base % 2 == 0,
        "uuid": uuid.UUID(int=1234567890 + base),
        "date": datetime.fromtimestamp(1704063600 + base).astimezone().isoformat(),
        "ints": [base, base + 1, base + 2],
        "numbers": [base + 0.5, base + 1.5, base + 2.5],
        "texts": [f"text{base}", f"text{base+1}", f"text{base+2}"],
        "booleans": [base % 2 == 0, base % 2 != 0],
        "uuids": [
            uuid.UUID(int=9876543210 + base),
            uuid.UUID(int=9876543211 + base),
            uuid.UUID(int=9876543212 + base),
        ],
        "dates": [
            datetime.fromtimestamp(1735686000 + base).astimezone().isoformat(),
            datetime.fromtimestamp(1735686001 + base).astimezone().isoformat(),
            datetime.fromtimestamp(1735686002 + base).astimezone().isoformat(),
        ],
        "phone": {"defaultCountry": "pl", "input": f"{100_000_000+base}"},
        "geo": {
            "latitude": (baseGeo % 18_000) / 100 - 90,
            "longitude": (baseGeo % 36_000) / 100 - 180,
        },
        "object": {
            "n_int": base,
            "n_number": base + 0.5,
            "n_text": f"text{base}",
            "n_boolean": base % 2 == 0,
            "n_uuid": uuid.UUID(int=1234567890 + base),
            "n_date": datetime.fromtimestamp(1704063600 + base).astimezone().isoformat(),
            "n_object": {
                "nn_int": base,
            },
        },
        "objects": [
            {
                "n_ints": [base, base + 1, base + 2],
                "n_numbers": [base + 0.5, base + 1.5, base + 2.5],
                "n_texts": [f"text{base}", f"text{base+1}", f"text{base+2}"],
                "n_booleans": [base % 2 == 0, base % 2 != 0],
                "n_uuids": [
                    uuid.UUID(int=9876543210 + base),
                    uuid.UUID(int=9876543211 + base),
                    uuid.UUID(int=9876543212 + base),
                ],
                "n_dates": [
                    datetime.fromtimestamp(1735686000 + base).astimezone().isoformat(),
                    datetime.fromtimestamp(1735686001 + base).astimezone().isoformat(),
                    datetime.fromtimestamp(1735686002 + base).astimezone().isoformat(),
                ],
                "n_objects": [
                    {
                        "nn_ints": [base, base + 1, base + 2],
                    }
                ],
            },
        ],
    }


def wait_for_all_shards_ready(collection: weaviate.collections.Collection):
    status = [s.status for s in collection.config.get_shards()]
    if not all(s == "READONLY" for s in status):
        raise Exception(f"shards are not READONLY at beginning: {status}")

    max_wait = 300
    before = time.time()

    while True:
        time.sleep(3)
        status = [s.status for s in collection.config.get_shards()]
        if all(s == "READY" for s in status):
            logger.info(f"finished in {time.time()-before}s")
            return

        if time.time() - before > max_wait:
            raise Exception(f"after {max_wait}s not all shards READY: {status}")
