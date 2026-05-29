from loguru import logger
import json
import weaviate
from weaviate.classes.config import Property, DataType, Configure, ReferenceProperty
from weaviate.classes.query import Filter, QueryReference
from weaviate.classes.data import DataReference
from weaviate.util import generate_uuid5
from weaviate.collections.classes.config import ConsistencyLevel
from graphql_aggregate import graphql_grpc_aggregate


def create(client: weaviate.WeaviateClient, admin_client: weaviate.WeaviateClient):
    _create_schema(client)
    _import(client, admin_client)


def _create_schema(client: weaviate.WeaviateClient):
    _create_books_schema(client)
    _create_authors_schema(client)


def _import(client: weaviate.WeaviateClient, admin_client: weaviate.WeaviateClient):
    _import_books(client, admin_client)
    _import_authors(client, admin_client)


def sanity_checks(client: weaviate.WeaviateClient):
    _books_sanity_checks(client)
    _authors_sanity_checks(client)


def _create_books_schema(client: weaviate.WeaviateClient):
    logger.info("create Books collection")
    books = client.collections.create(
        name="Books",
        properties=[
            Property(name="uuid", data_type=DataType.UUID),
            Property(name="author", data_type=DataType.TEXT),
            Property(name="title", data_type=DataType.TEXT),
            Property(name="description", data_type=DataType.TEXT),
            Property(name="genre", data_type=DataType.TEXT),
            Property(name="page_count", data_type=DataType.INT),
        ],
        replication_config=Configure.replication(factor=2, async_enabled=True),
        vectorizer_config=[
            Configure.NamedVectors.text2vec_contextionary(
                name="title",
                source_properties=["title"],
                vectorize_collection_name=False,
                vector_index_config=Configure.VectorIndex.hnsw(
                    quantizer=Configure.VectorIndex.Quantizer.pq(),
                ),
            ),
            Configure.NamedVectors.text2vec_contextionary(
                name="description",
                source_properties=["description"],
                vectorize_collection_name=False,
                vector_index_config=Configure.VectorIndex.flat(),
            ),
        ],
    )
    assert books is not None
    assert books.name == "Books"


def _create_authors_schema(client: weaviate.WeaviateClient):
    logger.info("create Authors collection")
    authors = client.collections.create(
        name="Authors",
        properties=[
            Property(name="author", data_type=DataType.TEXT),
            Property(name="genres", data_type=DataType.TEXT_ARRAY),
        ],
        references=[ReferenceProperty(name="wroteBooks", target_collection="Books")],
        replication_config=Configure.replication(factor=2, async_enabled=True),
        vectorizer_config=[
            Configure.NamedVectors.text2vec_contextionary(
                name="author",
                source_properties=["author"],
                vectorize_collection_name=False,
                vector_index_config=Configure.VectorIndex.hnsw(
                    quantizer=Configure.VectorIndex.Quantizer.pq(),
                ),
            ),
        ],
    )
    assert authors is not None
    assert authors.name == "Authors"


def _import_books(client: weaviate.WeaviateClient, admin_client: weaviate.WeaviateClient):
    books_json = "data/books/books.json"
    with open(books_json) as f:
        books = json.load(f)
        logger.info("import {} books", len(books))
        collection = client.collections.get("Books").with_consistency_level(
            consistency_level=ConsistencyLevel.ALL
        )
        with collection.batch.dynamic() as batch:
            for book in books:
                batch.add_object(properties=book, uuid=book["uuid"])
        assert (
            len(collection.batch.failed_objects) == 0
        ), f"expected no failures during books batch insert but got {len(collection.batch.failed_objects)}"
        logger.info("waiting for Books vector indexing to finish")
        # wait_for_vector_indexing() calls GET /schema/{class}/shards, which Weaviate
        # authorizes via read_tenants. Under RBAC, the workload user lacks that on Books*,
        # so the runner passes a separate admin client for the readiness wait.
        admin_client.collections.get("Books").batch.wait_for_vector_indexing()
        logger.info("Books vector indexing finished")


def _import_authors(client: weaviate.WeaviateClient, admin_client: weaviate.WeaviateClient):
    books_json = "data/books/books.json"
    authors_books = dict()
    authors_genres = dict()
    with open(books_json) as f:
        books = json.load(f)
        for book in books:
            author = book["author"]
            genre = book["genre"]
            if author in authors_books:
                authors_books[author].append(book["uuid"])
                if genre not in authors_genres[author]:
                    authors_genres[author].append(genre)
            else:
                authors_books[author] = [
                    book["uuid"],
                ]
                authors_genres[author] = [
                    genre,
                ]

        logger.info("import {} Authors", len(authors_books))
        authors = client.collections.get("Authors").with_consistency_level(
            consistency_level=ConsistencyLevel.ALL
        )
        author_uuids = dict()
        with authors.batch.dynamic() as batch:
            for author in authors_books:
                properties = {
                    "author": author,
                    "genres": authors_genres[author],
                }
                uuid = batch.add_object(properties=properties, uuid=generate_uuid5(properties))
                author_uuids[author] = uuid
        assert (
            len(authors.batch.failed_objects) == 0
        ), f"expected no failures during authors batch insert but got {len(authors.batch.failed_objects)}"
        logger.info("waiting for Authors vector indexing to finish")
        # See note in _import_books: shards-readiness requires read_tenants under RBAC.
        admin_client.collections.get("Authors").batch.wait_for_vector_indexing()
        logger.info("Authors vector indexing finished")

        refs_list = []
        for author in author_uuids:
            for book_uuid in authors_books[author]:
                ref_obj = DataReference(
                    from_uuid=author_uuids[author], from_property="wroteBooks", to_uuid=book_uuid
                )
                refs_list.append(ref_obj)
        refs_result = authors.data.reference_add_many(refs_list)
        assert refs_result is not None
        assert refs_result.has_errors is False


def _books_sanity_checks(client: weaviate.WeaviateClient):
    logger.info("running Books sanity checks")
    collection = client.collections.get("Books").with_consistency_level(
        consistency_level=ConsistencyLevel.ONE
    )
    aggregate = collection.aggregate.over_all()
    logger.info("aggregate total_count: {}", aggregate.total_count)
    assert aggregate.total_count == 54
    aggregate = collection.aggregate.over_all(
        filters=Filter.by_property("author").equal("Margaret Atwood")
    )
    logger.info("aggregate where author eq 'Margaret Atwood': {}", aggregate.total_count)
    assert aggregate.total_count == 3
    aggregate = collection.aggregate.over_all(
        filters=Filter.by_property("genre").equal("science-fiction")
    )
    logger.info("aggregate where genre eq 'science-fiction': {}", aggregate.total_count)
    assert aggregate.total_count == 34
    aggregate = collection.aggregate.over_all(
        filters=Filter.by_property("page_count").greater_than(800)
    )
    logger.info("aggregate where page_count gt 800: {}", aggregate.total_count)
    assert aggregate.total_count == 4
    result = collection.query.near_text(
        query=["Essos", "Westeros", "Throne"], target_vector="description", certainty=0.74
    )
    assert len(result.objects) == 1
    assert result.objects[0].properties["title"] == "A Game of Thrones"
    logger.info(
        "nearText query for Game of Thrones found: {}", result.objects[0].properties["title"]
    )
    result = collection.query.hybrid(
        query="Westeros", target_vector="description", return_metadata=["score"]
    )
    assert len(result.objects) > 0
    assert result.objects[0].properties["title"] == "A Game of Thrones"
    assert result.objects[0].metadata.score > 0
    logger.info(
        "hybrid query for Game of Thrones found with score: {}", result.objects[0].metadata.score
    )
    result = collection.query.fetch_objects(filters=Filter.by_property("genre").equal("cyberpunk"))
    assert len(result.objects) == 5
    logger.info("where genre equals cyberpunk found {} objects", len(result.objects))
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
    assert graphql_grpc_aggregate(client, "Books")


def _authors_sanity_checks(client: weaviate.WeaviateClient):
    logger.info("running Authors sanity checks")
    collection = client.collections.get("Authors").with_consistency_level(
        consistency_level=ConsistencyLevel.ONE
    )
    aggregate = collection.aggregate.over_all()
    logger.info("aggregate total_count: {}", aggregate.total_count)
    assert aggregate.total_count == 32
    result = collection.query.fetch_objects(
        filters=Filter.by_property("author").equal("George Orwell"),
        return_references=[
            QueryReference(link_on="wroteBooks", return_properties=["title"]),
        ],
    )
    assert result is not None
    assert len(result.objects) == 1
    assert result.objects[0].references is not None
    assert result.objects[0].references["wroteBooks"] is not None
    assert len(result.objects[0].references["wroteBooks"].objects) == 2
    titles = []
    for obj in result.objects[0].references["wroteBooks"].objects:
        titles.append(obj.properties["title"])
    for title in ["Nineteen Eighty-Four", "Animal Farm"]:
        assert title in titles
    logger.info("where query for George Orwell's books found: {}", titles)
    result = collection.query.fetch_objects(
        filters=Filter.all_of(
            [
                Filter.by_property("author").equal("George Orwell"),
                Filter.by_property("genres").contains_all(["dystopian", "political satire"]),
            ]
        ),
        return_references=[
            QueryReference(link_on="wroteBooks", return_properties=["title"]),
        ],
    )
    assert result is not None
    assert len(result.objects) == 1
    assert result.objects[0].references is not None
    assert result.objects[0].references["wroteBooks"] is not None
    assert len(result.objects[0].references["wroteBooks"].objects) == 2
    titles = []
    for obj in result.objects[0].references["wroteBooks"].objects:
        titles.append(obj.properties["title"])
    for title in ["Nineteen Eighty-Four", "Animal Farm"]:
        assert title in titles
    logger.info("where with contains all query for George Orwell's books found: {}", titles)
    result = collection.query.fetch_objects(
        filters=Filter.by_ref("wroteBooks").by_property("genre").equal("dystopian"),
        return_references=[
            QueryReference(link_on="wroteBooks", return_properties=["title"]),
        ],
    )
    assert result is not None
    assert (
        len(result.objects) == 5
    ), f"expected filter by wroteBooks reference to return 5 results but got {len(result.objects)}"
    logger.info(
        "where on reference Books genre property equal to dystopian found: {}", len(result.objects)
    )
    assert graphql_grpc_aggregate(client, "Authors")
