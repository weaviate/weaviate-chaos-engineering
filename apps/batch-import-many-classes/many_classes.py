import weaviate
import weaviate.classes as wvc
import time
import random
import sys
from loguru import logger

client = weaviate.connect_to_local(timeout=(20, 240))

timeout = 5

checkpoint = time.time()
interval = 100
classes: list[str] = []
for i in range(500):
    if i != 0 and i % interval == 0:
        avg = (time.time() - checkpoint) / interval
        logger.info(f"avg create duration is {avg} over past {interval} creates")
        checkpoint = time.time()

    before = time.time()
    classes.append("Article" + str(i))
    client.collections.create(
        name="Article" + str(i),
        description="A written text, for example a news article or blog post",
        properties=[
            wvc.Property(
                name="title",
                data_type=wvc.DataType.TEXT,
            ),
            wvc.Property(
                name="content",
                data_type=wvc.DataType.TEXT,
            ),
            wvc.Property(
                name="int",
                data_type=wvc.DataType.INT,
            ),
            wvc.Property(
                name="number",
                data_type=wvc.DataType.NUMBER,
            ),
        ]
    )
    took = time.time() - before
    if took > timeout:
        logger.error(f"last class action took {took}s, but toleration limit is {timeout}s")
        sys.exit(1)


random.shuffle(classes)
i = 0
checkpoint = time.time()
interval = 10
while True:
    if i != 0 and i % interval == 0:
        logger.info(
            f"avg delete duration is {(time.time()-checkpoint)/interval} over past {interval} deletes"
        )
        checkpoint = time.time()

    if len(classes) == 0:
        break

    before = time.time()
    client.collections.delete(classes.pop())
    took = time.time() - before
    if took > timeout:
        logger.error(f"last class action took {took}s, but toleration limit is {timeout}s")
        sys.exit(1)
    i += 1
