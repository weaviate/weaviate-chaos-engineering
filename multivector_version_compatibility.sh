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
  "1.28.4"
)

declare -a drop_multivector_versions=(
    "1.28.6"
    "1.27.10"
    "1.26.13"
)

declare -a full_multivector_versions=(
  "1.29.0"
)

function test_version_sequence() {
    local intermediate_version=$1
    local multivector_support=$2

    echo "Testing version sequence: $WEAVIATE_VERSION -> $intermediate_version -> $WEAVIATE_VERSION"
    
    # remove any files in docker volumes
    docker compose -f apps/weaviate/docker-compose.yml rm -v

    # Test with WEAVIATE_VERSION
    echo "Starting Weaviate version $WEAVIATE_VERSION..."
    docker compose -f apps/weaviate/docker-compose.yml up -d
    wait_weaviate

    # import data
    echo "Ingesting multivectors"
    docker run --network host -t multivector_version_compatibility python3 ingest_multivectors.py

    # verify on start version
    echo "Verifying multivectors"
    docker run --network host -t multivector_version_compatibility python3 verify_multivectors.py

    # shutdown weaviate
    docker compose -f apps/weaviate/docker-compose.yml down

    # Test with intermediate version
    echo "Starting Weaviate version $intermediate_version"
    WEAVIATE_VERSION=$intermediate_version docker compose -f apps/weaviate/docker-compose.yml up -d
    wait_weaviate

    # verify queryable on intermediate version
    docker run --network host -t multivector_version_compatibility python3 verify_multivectors.py --multivector-support "$multivector_support"

    # shutdown weaviate
    docker compose -f apps/weaviate/docker-compose.yml down

    # Test with WEAVIATE_VERSION again
    echo "Starting Weaviate version $WEAVIATE_VERSION again"
    docker compose -f apps/weaviate/docker-compose.yml up -d
    wait_weaviate

    # verify queryable on multivectors after going through intermediate version
    docker run --network host -t multivector_version_compatibility python3 verify_multivectors.py

    # shutdown weaviate
    docker compose -f apps/weaviate/docker-compose.yml down

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
