from loguru import logger
import json, time
import weaviate
from weaviate.classes.config import Property, DataType, Configure
from weaviate.classes.query import Filter
from weaviate.classes.tenants import Tenant
from weaviate.collections.classes.config import ConsistencyLevel
from graphql_aggregate import graphql_grpc_aggregate


number_of_tenants = 300
additional_number_of_tenants = 100


def create(client: weaviate.WeaviateClient):
    for i in range(number_of_tenants):
        _create_multitenancy_schema(client, i)
    for i in range(number_of_tenants):
        _import_books(client, i)


def check_collections_existence(client: weaviate.WeaviateClient):
    if not _wait_for_collections_to_exist(client):
        raise Exception("not all multitenancy collections exist, stopping")


def sanity_checks(client: weaviate.WeaviateClient):
    for i in range(number_of_tenants):
        _books_sanity_checks(client, i)
    logger.info("[sanity_checks] going further")


def create_additional(client: weaviate.WeaviateClient):
    suffix = "Additional"
    for i in range(additional_number_of_tenants):
        _create_multitenancy_schema(client, i, suffix=suffix)
    for i in range(additional_number_of_tenants):
        _import_books(client, i, suffix=suffix)


def check_additional_collections_existence(client: weaviate.WeaviateClient):
    suffix = "Additional"
    if not _wait_for_collections_to_exist(client, suffix):
        raise Exception("not all multitenancy collections exist, stopping")


def sanity_checks_additional(client: weaviate.WeaviateClient):
    for i in range(additional_number_of_tenants):
        _books_sanity_checks(client, i, suffix="Additional")


def _get_class_name(i: int, suffix: str = "") -> str:
    return f"MTClass{suffix}_{i}"


def _get_tenant_name(i: int, suffix: str = "") -> str:
    return f"tenant{suffix}_{i}"


def _create_multitenancy_schema(client: weaviate.WeaviateClient, i: int, suffix: str = ""):
    class_name = _get_class_name(i, suffix)
    tenant = _get_tenant_name(i, suffix)
    logger.info("create {} collection with tenant {}", class_name, tenant)
    collection = client.collections.create(
        name=class_name,
        properties=[
            Property(name="uuid", data_type=DataType.UUID),
            Property(name="author", data_type=DataType.TEXT),
            Property(name="title", data_type=DataType.TEXT),
            Property(name="description", data_type=DataType.TEXT),
            Property(name="genre", data_type=DataType.TEXT),
            Property(name="page_count", data_type=DataType.INT),
        ],
        replication_config=Configure.replication(factor=2),
        vectorizer_config=[
            Configure.NamedVectors.text2vec_contextionary(
                name="description",
                source_properties=["description"],
                vectorize_collection_name=False,
                vector_index_config=Configure.VectorIndex.flat(),
            )
        ],
        multi_tenancy_config=Configure.multi_tenancy(True),
    )
    assert collection is not None
    assert collection.name == class_name

    collection.tenants.create(tenants=[Tenant(name=tenant)])


def _import_books(client: weaviate.WeaviateClient, i: int, suffix: str = ""):
    class_name = _get_class_name(i, suffix)
    tenant = _get_tenant_name(i, suffix)
    books_json = "data/books/books.json"
    with open(books_json) as f:
        books = json.load(f)
        logger.info("import {} {} with tenant {}", len(books), class_name, tenant)
        collection = (
            client.collections.get(class_name)
            .with_tenant(tenant)
            .with_consistency_level(consistency_level=ConsistencyLevel.ONE)
        )
        collection_with_tenant = collection.with_tenant(tenant)
        with collection_with_tenant.batch.dynamic() as batch:
            for book in books:
                batch.add_object(
                    properties=book,
                    uuid=book["uuid"],
                )
            batch.flush()


def _wait_for_collections_to_exist(client: weaviate.WeaviateClient, suffix: str = "") -> bool:
    sec, cutoff = 0, 120
    while sec < cutoff:
        sec = sec + 1
        exists = _all_collections_exists(client, number_of_tenants)
        if suffix != "":
            exists = exists and _all_collections_exists(
                client, additional_number_of_tenants, suffix
            )
        if exists:
            logger.info("all multitenancy collections exist, procced with sanity checks")
            return True
        logger.warning("not all multitenancy collections exist, waiting for 1s to retry...")
        time.sleep(1)

    logger.error(
        "multitenancy collections check timed out after {}s, multitenancy collections don't exist",
        cutoff,
    )
    return False


def _all_collections_exists(
    client: weaviate.WeaviateClient, num_of_tenants: int, suffix: str = ""
) -> bool:
    for i in range(num_of_tenants):
        if not client.collections.exists(_get_class_name(i, suffix)):
            return False
    # all collections exist
    return True


def _books_sanity_checks(client: weaviate.WeaviateClient, i: int, suffix: str = ""):
    class_name = _get_class_name(i, suffix)
    tenant = _get_tenant_name(i, suffix)
    logger.debug("running {} sanity checks with tenant {}", class_name, tenant)
    assert graphql_grpc_aggregate(client, class_name, tenant)
    collection = (
        client.collections.get(class_name)
        .with_tenant(tenant)
        .with_consistency_level(consistency_level=ConsistencyLevel.ONE)
    )
    result = collection.query.near_text(
        query=["Essos", "Westeros", "Throne"], target_vector="description", certainty=0.75
    )
    assert len(result.objects) == 1
    assert result.objects[0].properties["title"] == "A Game of Thrones"
    logger.debug(
        "nearText query for Game of Thrones found: {}", result.objects[0].properties["title"]
    )
    result = collection.query.hybrid(
        query="Westeros", target_vector="description", return_metadata=["score"]
    )
    assert len(result.objects) > 0
    assert result.objects[0].properties["title"] == "A Game of Thrones"
    assert result.objects[0].metadata.score > 0
    logger.debug(
        "hybrid query for Game of Thrones found with score: {}", result.objects[0].metadata.score
    )
    result = collection.query.fetch_objects(filters=Filter.by_property("genre").equal("cyberpunk"))
    assert len(result.objects) == 5
    logger.debug("where genre equals cyberpunk found {} objects", len(result.objects))
    result = collection.query.near_text(
        filters=Filter.by_property("genre").equal("cyberpunk"),
        query="hacker in America",
        target_vector="description",
        certainty=0.75,
    )
    assert len(result.objects) == 1
    assert result.objects[0].properties["title"] == "Snow Crash"
    logger.info(
        "nearText with where query for Snow Crash found: {}", result.objects[0].properties["title"]
    )
    aggregate = collection.aggregate.over_all()
    logger.debug("aggregate total_count: {}", aggregate.total_count)
    assert aggregate.total_count == 54
    aggregate = collection.aggregate.over_all(
        filters=Filter.by_property("author").equal("Margaret Atwood"),
    )
    logger.debug("aggregate where author eq 'Margaret Atwood': {}", aggregate.total_count)
    assert aggregate.total_count == 3
    aggregate = collection.aggregate.over_all(
        filters=Filter.by_property("genre").equal("science-fiction")
    )
    logger.debug("aggregate where genre eq 'science-fiction': {}", aggregate.total_count)
    assert aggregate.total_count == 34
    aggregate = collection.aggregate.over_all(
        filters=Filter.by_property("page_count").greater_than(800)
    )
    logger.debug("aggregate where page_count gt 800: {}", aggregate.total_count)
    assert aggregate.total_count == 4
    logger.success("success {} sanity checks with tenant {}", class_name, tenant)
