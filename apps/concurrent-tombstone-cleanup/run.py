import numpy as np
import weaviate
import weaviate.classes as wvc
import time

from loguru import logger


client = weaviate.connect_to_local()
client.collections.delete_all()

col = client.collections.create(
    "MyCol",
    vector_index_config=wvc.config.Configure.VectorIndex.hnsw(cleanup_interval_seconds=60),
)

start = time.time()
with col.batch.fixed_size(100, 10) as batch:
    for j in range(50_000):
        batch.add_object(
            {"always_true": True, "i": j},
            vector=np.random.rand(1, 1536)[0].tolist(),
        )
took = time.time() - start
logger.info(f"batch imported in {took:.2f}s")

while True:
    try:
        start = time.time()
        res = col.data.delete_many(where=wvc.query.Filter.by_property("i").greater_than(10))
        took = time.time() - start
        logger.info(f"batch deleted in {took:.2f}s")
        if res.matches == 0:
            logger.info("test complete!")
            break
    except Exception as e:
        logger.error(e)
