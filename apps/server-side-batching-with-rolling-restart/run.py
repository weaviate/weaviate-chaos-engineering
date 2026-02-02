import asyncio
import json
import random
import sys
import time
import weaviate
import weaviate.classes.config as wvcc


def setup(client: weaviate.WeaviateClient, collection: str) -> weaviate.collections.Collection:
    if client.collections.exists(collection):
        client.collections.delete(collection)
    return client.collections.create(
        name=collection,
        properties=[
            wvcc.Property(
                name="title",
                data_type=wvcc.DataType.TEXT,
            ),
            wvcc.Property(
                name="content",
                data_type=wvcc.DataType.TEXT,
            ),
        ],
        replication_config=wvcc.Configure.replication(factor=3, async_enabled=True),
        vector_config=wvcc.Configure.Vectors.self_provided(),
    )


def import_sync(
    client: weaviate.WeaviateClient, collection: str, how_many: int = 1_000_000
) -> None:
    uuids: dict[str, int] = {}
    with client.batch.stream(concurrency=1) as batch:
        for i in range(how_many):
            uuid = batch.add_object(
                collection=collection,
                properties={
                    "title": f"Title {i}",
                    "content": f"Content {i}",
                },
                vector=random_vector(),
            )
            uuids[str(uuid)] = i

    for err in client.batch.failed_objects:
        print(err.message)
    if len(client.batch.failed_objects) > 0:
        print(
            f"Expected there to be no errors when importing but there were {len(client.batch.failed_objects)}. Check above logs for details"
        )
        sys.exit(1)
    client.batch.wait_for_vector_indexing()
    with open("uuids.json", "w") as f:
        json.dump(uuids, f)


async def import_async(
    client: weaviate.WeaviateAsyncClient, collection: str, how_many: int = 1_000_000
) -> None:
    uuids: dict[str, int] = {}
    async with client.batch.stream(concurrency=1) as batch:
        for i in range(how_many):
            uuid = await batch.add_object(
                collection=collection,
                properties={
                    "title": f"Title {i}",
                    "content": f"Content {i}",
                },
                vector=random_vector(),
            )
            uuids[str(uuid)] = i

    for err in client.batch.failed_objects:
        print(err.message)
    if len(client.batch.failed_objects) > 0:
        print(
            f"Expected there to be no errors when importing but there were {len(client.batch.failed_objects)}. Check above logs for details"
        )
        sys.exit(1)
    await client.batch.wait_for_vector_indexing()
    with open("uuids.json", "w") as f:
        json.dump(uuids, f)


def verify(client: weaviate.WeaviateClient, collection: str, expected: int = 1_000_000) -> None:
    actual = 0
    count = 0
    c = client.collections.use(collection)
    while actual < expected:
        actual = len(c)
        print(f"Found {actual} objects, waiting for async repl to reach {expected}...")
        time.sleep(1)
        count += 1
        if count == 600:  # 10 minutes
            break
    if actual > expected:
        print(f"Expected at most {expected} objects, found {actual}")
        sys.exit(1)
    if actual != expected:
        print(
            f"Expected {expected} objects, found {actual} after 10 minutes of waiting for async replication to complete"
        )
        actual_ids = []
        for obj in c.iterator():
            actual_ids.append(int(obj.properties["title"].split(" ")[1]))  # pyright: ignore
        expected_ids = list(range(expected))
        print(f"Missing IDs: {sorted(list(set(expected_ids).difference(actual_ids)))}")
        sys.exit(1)


def random_vector() -> list[float]:
    return [random.uniform(0, 1) for _ in range(128)]


def sync() -> None:
    collection = "BatchImportShutdownJourney"
    how_many = 100000
    with weaviate.connect_to_local() as client:
        collection = setup(client, collection)
        import_sync(client, collection.name, how_many)
        verify(client, collection.name, how_many)
        print("Journey completed successfully")


async def async_() -> None:
    collection = "BatchImportShutdownJourney"
    how_many = 100000
    with weaviate.connect_to_local() as client:
        collection = setup(client, collection)
        async with weaviate.use_async_with_local() as aclient:
            await import_async(aclient, collection.name, how_many)
        verify(client, collection.name, how_many)
        print("Journey completed successfully")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python run.py [sync|async]")
        sys.exit(1)

    if sys.argv[1] == "sync":
        sync()
    elif sys.argv[1] == "async":
        asyncio.run(async_())
    else:
        print("Invalid argument. Use 'sync' or 'async'.")
        sys.exit(1)
