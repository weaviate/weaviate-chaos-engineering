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

# TODO should i test starting from "old", going to "new", and then back to "old"? eg opposite?
# technically ppl can create msgpack on 1.28 though might not be available via api

# too old, error msg says at least 1.23.7 needed
# "1.21.0 1.21.9"
# "1.22.0 1.22.13"
# "1.23.0 1.23.6"

# error msg says named vectorizers are only supported in Weaviate v1.24.0 and higher
# "1.23.7 1.23.16"

# to test go from 1.28.2 to 1.28.X and vice versa

# TODO add tests for going from new->old->new, old->new->old, etc
# TODO read and write in all phases
# TODO test 1.28 with msgpack and without



    # 1.25.* verify_multivectors.py", line ~56 fails with: The number of target vectors must be equal to the number of vectors.
    # "1.25.29"
    # "1.25.0"

    # 1.24.* verify_multivectors.py", line ~30 fails with: AssertionError: Expected 1 object in Normalvector, got 0
    # "1.24.26"
    # "1.24.0"

  # TODO these are really broken
    # "1.28.4"
    # "1.28.0"
    # "1.28.4-68dc579"
    # "1.28.4-8af02e7"

# Define versions to test
declare -a versions=(
    "1.28.4-1a67582"
    "1.27.10"
    "1.27.0"
    "1.26.13"
    "1.26.0"
)

echo "Building all required containers"
( cd apps/multivector-version-compatibility/ && docker build -t multivector_version_compatibility . )

for intermediate_version in "${versions[@]}"; do
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
    docker run --network host -t multivector_version_compatibility python3 verify_multivectors.py --multivector-support DROPS_MULTIVECTOR

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
done

echo "All versions tested successfully!"
