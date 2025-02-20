#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080/v1/.well-known/ready; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready after 120s"
  exit 1
}


declare -a no_multivector_versions=(
# Traceback (most recent call last):
#   File "/workdir/verify_multivectors.py", line 43, in <module>
#     assert len(normal_objects.objects) == 1, f"Expected 1 object in {NORMAL_VECTOR_COLLECTION_NAME}, got {len(normal_objects.objects)}"
#            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
# AssertionError: Expected 1 object in Normalvector, got 0
  # "1.24.26"

# Traceback (most recent call last):
#   File "/workdir/verify_multivectors.py", line 71, in <module>
#     normal_results = normal_collection.query.near_vector(
#                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/usr/local/lib/python3.11/site-packages/weaviate/syncify.py", line 23, in sync_method
#     return _EventLoopSingleton.get_instance().run_until_complete(
#            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/usr/local/lib/python3.11/site-packages/weaviate/event_loop.py", line 42, in run_until_complete
#     return fut.result()
#            ^^^^^^^^^^^^
#   File "/usr/local/lib/python3.11/concurrent/futures/_base.py", line 456, in result
#     return self.__get_result()
#            ^^^^^^^^^^^^^^^^^^^
#   File "/usr/local/lib/python3.11/concurrent/futures/_base.py", line 401, in __get_result
#     raise self._exception
#   File "/usr/local/lib/python3.11/site-packages/weaviate/collections/queries/near_vector/query.py", line 92, in near_vector
#     res = await self._query.near_vector(
#                 ^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/usr/local/lib/python3.11/site-packages/weaviate/collections/grpc/query.py", line 248, in near_vector
#     near_vector=self._parse_near_vector(
#                 ^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/usr/local/lib/python3.11/site-packages/weaviate/collections/grpc/shared.py", line 376, in _parse_near_vector
#     vector_per_target_tmp, near_vector_grpc = self._vector_per_target(
#                                               ^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/usr/local/lib/python3.11/site-packages/weaviate/collections/grpc/shared.py", line 129, in _vector_per_target
#     raise WeaviateInvalidInputError(
# weaviate.exceptions.WeaviateInvalidInputError: Invalid input provided: The number of target vectors must be equal to the number of vectors..
  # "1.25.29"
  "1.28.4"
)

# TODO can we easily use "latest" patch instead of specific versions?
declare -a drop_multivector_versions=(
    # "1.28.6"
    # "1.27.10"
    # "1.26.13"
)

function test_version_sequence() {
    local intermediate_version=$1
    local multivector_support=$2

    echo "Testing version sequence: $WEAVIATE_VERSION -> $intermediate_version -> $WEAVIATE_VERSION"
    
    # remove any files in docker volumes
    docker-compose -f apps/weaviate/docker-compose.yml rm -v

    # Test with WEAVIATE_VERSION
    echo "Starting Weaviate version $WEAVIATE_VERSION..."
    docker-compose -f apps/weaviate/docker-compose.yml up -d
    wait_weaviate

    # import data
    echo "Ingesting multivectors"
    docker run --network host -t multivector_version_compatibility python3 ingest_multivectors.py

    # verify on start version
    echo "Verifying multivectors"
    docker run --network host -t multivector_version_compatibility python3 verify_multivectors.py

    # shutdown weaviate
    docker-compose -f apps/weaviate/docker-compose.yml down

    # Test with intermediate version
    echo "Starting Weaviate version $intermediate_version"
    WEAVIATE_VERSION=$intermediate_version docker-compose -f apps/weaviate/docker-compose.yml up -d
    wait_weaviate

    # verify queryable on intermediate version
    docker run --network host -t multivector_version_compatibility python3 verify_multivectors.py --multivector-support "$multivector_support"

    # shutdown weaviate
    docker-compose -f apps/weaviate/docker-compose.yml down

    # Test with WEAVIATE_VERSION again
    echo "Starting Weaviate version $WEAVIATE_VERSION again"
    docker-compose -f apps/weaviate/docker-compose.yml up -d
    wait_weaviate

    # verify queryable on multivectors after going through no multivector support
    docker run --network host -t multivector_version_compatibility python3 verify_multivectors.py

    # shutdown weaviate
    docker-compose -f apps/weaviate/docker-compose.yml down

    echo "Completed testing version sequence: $WEAVIATE_VERSION -> $intermediate_version -> $WEAVIATE_VERSION"
    echo "----------------------------------------"
}

echo "Building all required containers"
( cd apps/multivector-version-compatibility/ && docker build -t multivector_version_compatibility . )

for intermediate_version in "${drop_multivector_versions[@]}"; do
    test_version_sequence "$intermediate_version" "DROPS_MULTIVECTOR"
done

for intermediate_version in "${no_multivector_versions[@]}"; do
    test_version_sequence "$intermediate_version" "NONE"
done

echo "All versions tested successfully!"
