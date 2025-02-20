import weaviate
import weaviate.classes.config as wvc

# Collection names
NORMAL_VECTOR_COLLECTION_NAME = "Normalvector"
MULTIVECTOR_COLLECTION_NAME = "Multivector"
BOTH_COLLECTION_NAME = "Both"
NORMAL_VECTOR_NAME = "normal"
MULTIVECTOR_NAME = "multi"

# TODO flag to do only normal vectors or both

# Example properties
# Example vectors
NORMAL_VECTOR = {
    NORMAL_VECTOR_NAME: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
}
NORMAL_VECTOR_QUERY = {
    NORMAL_VECTOR_NAME: NORMAL_VECTOR[NORMAL_VECTOR_NAME],
}
MULTI_VECTOR = {
    MULTIVECTOR_NAME: [[0.1, 0.1], [0.2, 0.2]],
}
MULTI_VECTOR_QUERY = {
    MULTIVECTOR_NAME: weaviate.classes.query.NearVector.list_of_vectors(MULTI_VECTOR[MULTIVECTOR_NAME]),
}
# merge
BOTH_VECTORS = {
    **NORMAL_VECTOR,
    **MULTI_VECTOR,
}
BOTH_VECTORS_QUERY = {
    **NORMAL_VECTOR_QUERY,
    **MULTI_VECTOR_QUERY,
}
NORMAL_VECTOR_PROPERTIES = {"name": "Normal"}
MULTI_VECTOR_PROPERTIES = {"name": "Multi-Vector"}
BOTH_VECTORS_PROPERTIES = {"name": "Both"}


def main():
    client = weaviate.connect_to_local()

    # Delete collections
    client.collections.delete(NORMAL_VECTOR_COLLECTION_NAME)
    client.collections.delete(MULTIVECTOR_COLLECTION_NAME)
    client.collections.delete(BOTH_COLLECTION_NAME)

    normal_vector_index_config = wvc.Configure.VectorIndex.hnsw(
        ef=512,
    )
    multi_vector_index_config = wvc.Configure.VectorIndex.hnsw(
        ef=512,
        multi_vector=wvc.Configure.VectorIndex.MultiVector.multi_vector(),
    )
    normal_vectorizer_config = [
        wvc.Configure.NamedVectors.none(
            name=NORMAL_VECTOR_NAME,
            vector_index_config=normal_vector_index_config,
        ),
    ]
    multi_vectorizer_config = [
        wvc.Configure.NamedVectors.none(
            name=MULTIVECTOR_NAME,
            vector_index_config=multi_vector_index_config,
        ),
    ]
    both_vectorizer_config = normal_vectorizer_config + multi_vectorizer_config

    # Create normal vector collection
    normal_collection = client.collections.create(
        name=NORMAL_VECTOR_COLLECTION_NAME,
        properties=[
            wvc.Property(
                name="name",
                data_type=wvc.DataType.TEXT,
            )
        ],
        vectorizer_config=normal_vectorizer_config,
        replication_config=wvc.Configure.replication(factor=1),
    )

    # Create multi-vector collection
    multi_collection = client.collections.create(
        name=MULTIVECTOR_COLLECTION_NAME,
        properties=[
            wvc.Property(
                name="name",
                data_type=wvc.DataType.TEXT,
            )
        ],
        vectorizer_config=multi_vectorizer_config,
        replication_config=wvc.Configure.replication(factor=1),
    )

    # Create collection with both vector types
    both_collection = client.collections.create(
        name=BOTH_COLLECTION_NAME,
        properties=[
            wvc.Property(
                name="name",
                data_type=wvc.DataType.TEXT,
            )
        ],
        vectorizer_config=both_vectorizer_config,
        replication_config=wvc.Configure.replication(factor=1),
    )

    # Insert example objects into each collection
    normal_collection.data.insert(
        properties=NORMAL_VECTOR_PROPERTIES,
        vector=NORMAL_VECTOR,
    )
    
    multi_collection.data.insert(
        properties=MULTI_VECTOR_PROPERTIES,
        vector=MULTI_VECTOR,
    )
    
    both_collection.data.insert(
        properties=BOTH_VECTORS_PROPERTIES,
        vector=BOTH_VECTORS,
    )

    client.close()


if __name__ == "__main__":
    main()
