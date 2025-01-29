import argparse
import numpy as np
import weaviate
import weaviate.classes.config as wvc

from ingest_multivectors import (
    NORMAL_VECTOR_COLLECTION_NAME,
    MULTIVECTOR_COLLECTION_NAME,
    BOTH_COLLECTION_NAME,
    NORMAL_VECTOR_NAME,
    MULTIVECTOR_NAME,
    NORMAL_VECTOR_PROPERTIES,
    MULTI_VECTOR_PROPERTIES,
    BOTH_VECTORS_PROPERTIES,
    NORMAL_VECTOR,
    MULTI_VECTOR,
    BOTH_VECTORS,
    MULTI_VECTOR_QUERY,
    BOTH_VECTORS_QUERY,
)


def vectors_match(v1, v2, rtol=1e-5):
    """Compare vectors with floating point tolerance."""
    if isinstance(v1, dict):
        return all(vectors_match(v1[k], v2[k], rtol) for k in v1)
    return np.allclose(v1, v2, rtol=rtol)


# Set up argument parser
parser = argparse.ArgumentParser(description='Verify multivector functionality')
parser.add_argument('--skip-multivector', action='store_true', 
                   help='Skip multivector-related queries (for older Weaviate versions)')
args = parser.parse_args()

client = weaviate.connect_to_local()

# Verify normal vector collection
normal_collection = client.collections.get(NORMAL_VECTOR_COLLECTION_NAME)
normal_objects = normal_collection.query.fetch_objects(include_vector=True)
assert len(normal_objects.objects) == 1, f"Expected 1 object in {NORMAL_VECTOR_COLLECTION_NAME}, got {len(normal_objects.objects)}"
obj = normal_objects.objects[0]
assert obj.properties == NORMAL_VECTOR_PROPERTIES, f"Properties mismatch in {NORMAL_VECTOR_COLLECTION_NAME}: expected {NORMAL_VECTOR_PROPERTIES}, got {obj.properties}"
assert vectors_match(obj.vector, NORMAL_VECTOR), f"Vector mismatch in {NORMAL_VECTOR_COLLECTION_NAME}"

# Verify multi-vector collection
multi_collection = client.collections.get(MULTIVECTOR_COLLECTION_NAME)
multi_objects = multi_collection.query.fetch_objects(include_vector=True)
assert len(multi_objects.objects) == 1, f"Expected 1 object in {MULTIVECTOR_COLLECTION_NAME}, got {len(multi_objects.objects)}"
obj = multi_objects.objects[0]
assert obj.properties == MULTI_VECTOR_PROPERTIES, f"Properties mismatch in {MULTIVECTOR_COLLECTION_NAME}: expected {MULTI_VECTOR_PROPERTIES}, got {obj.properties}"
if not args.skip_multivector:
    assert vectors_match(obj.vector, MULTI_VECTOR), f"Vector mismatch in {MULTIVECTOR_COLLECTION_NAME}"

# Verify collection with both vector types
both_collection = client.collections.get(BOTH_COLLECTION_NAME)
both_objects = both_collection.query.fetch_objects(include_vector=True)
assert len(both_objects.objects) == 1, f"Expected 1 object in {BOTH_COLLECTION_NAME}, got {len(both_objects.objects)}"
obj = both_objects.objects[0]
assert obj.properties == BOTH_VECTORS_PROPERTIES, f"Properties mismatch in {BOTH_COLLECTION_NAME}: expected {BOTH_VECTORS_PROPERTIES}, got {obj.properties}"
# check the normal vector but not the multi vector
assert vectors_match(obj.vector[NORMAL_VECTOR_NAME], NORMAL_VECTOR[NORMAL_VECTOR_NAME]), f"Vector mismatch in {BOTH_COLLECTION_NAME}"
if not args.skip_multivector:
    assert vectors_match(obj.vector, BOTH_VECTORS), f"Vector mismatch in {BOTH_COLLECTION_NAME}"

# Search normal vector collection
normal_results = normal_collection.query.near_vector(
    near_vector=NORMAL_VECTOR,
    # TODO why is target_vector needed?
    target_vector=NORMAL_VECTOR_NAME,
    include_vector=True,
    limit=10,
)
assert len(normal_results.objects) == 1, "Expected 1 result from normal vector search"
assert normal_results.objects[0].properties["name"] == NORMAL_VECTOR_PROPERTIES["name"], "Name mismatch in normal vector search"
assert vectors_match(normal_results.objects[0].vector, NORMAL_VECTOR), "Vector mismatch in normal vector search"

# Search multi-vector collection
if not args.skip_multivector:
    multi_results = multi_collection.query.near_vector(
        near_vector=MULTI_VECTOR_QUERY,
        target_vector=MULTIVECTOR_NAME,
        include_vector=True,
        limit=10,
    )
    assert len(multi_results.objects) == 1, "Expected 1 result from multi-vector search"
    assert multi_results.objects[0].properties["name"] == MULTI_VECTOR_PROPERTIES["name"], "Name mismatch in multi-vector search"
    assert vectors_match(multi_results.objects[0].vector, MULTI_VECTOR), "Vector mismatch in multi-vector search"

# Search both vectors collection
both_normal_results = both_collection.query.near_vector(
    near_vector=NORMAL_VECTOR,
    target_vector=NORMAL_VECTOR_NAME,
    include_vector=True,
    limit=10,
)
assert len(both_normal_results.objects) == 1, "Expected 1 result from both collection normal vector search"
assert both_normal_results.objects[0].properties["name"] == BOTH_VECTORS_PROPERTIES["name"], "Name mismatch in both vectors normal search"
assert vectors_match(both_normal_results.objects[0].vector[NORMAL_VECTOR_NAME], NORMAL_VECTOR[NORMAL_VECTOR_NAME]), "Vector mismatch in both vectors normal search"
if not args.skip_multivector:
    assert vectors_match(both_normal_results.objects[0].vector, BOTH_VECTORS), "Vector mismatch in both vectors normal search"

# TODO i think this is a bug in the client<->server grpc interaction? test with real server
# if not args.skip_multivector:
#     # extract target vectors: class Both has multiple vectors, but no target vectors were provided
#     both_multi_results = both_collection.query.near_vector(
#         near_vector=MULTI_VECTOR_QUERY,
#         # target_vector=MULTIVECTOR_NAME,
#         # target_vector=[MULTIVECTOR_NAME],
#         target_vector=weaviate.classes.query.TargetVectors.minimum([MULTIVECTOR_NAME]),
#         include_vector=True,
#         limit=10,
#     )
#     assert len(both_multi_results.objects) > 0, "Expected at least one result from both collection multi-vector search"
#     assert both_multi_results.objects[0].properties["name"] == BOTH_VECTORS_PROPERTIES["name"], "Name mismatch in both vectors multi search"
#     assert both_multi_results.objects[0].vector == BOTH_VECTORS, "Vector mismatch in both vectors multi search"

if not args.skip_multivector:
    # Search both vectors collection with both vector types simultaneously
    both_combined_results = both_collection.query.near_vector(
        near_vector=BOTH_VECTORS,
        target_vector=[NORMAL_VECTOR_NAME, MULTIVECTOR_NAME],
        include_vector=True,
        limit=10,
    )
    assert len(both_combined_results.objects) == 1, "Expected 1 result from both collection combined vector search"
    assert both_combined_results.objects[0].properties["name"] == BOTH_VECTORS_PROPERTIES["name"], "Name mismatch in both vectors combined search"
    assert vectors_match(both_combined_results.objects[0].vector, BOTH_VECTORS), "Vector mismatch in both vectors combined search"

client.close()
# curl -H "Content-Type: application/json" -X POST -d '{"query": "{Get {CollectionMultivector {name _additional{vectors{colbert}}}}}"}' localhost:8080/v1/graphql# 
