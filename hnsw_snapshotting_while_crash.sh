#!/bin/bash

set -e

source common.sh

export PERSISTENCE_HNSW_MAX_LOG_SIZE=5MB
export PERSISTENCE_HNSW_SNAPSHOT_INTERVAL_SECONDS=180
export PERSISTENCE_HNSW_DISABLE_SNAPSHOTS=false

SIZE=600000

run_importer() {
    local size=$1
    if ! docker run \
        -e 'DIMENSIONS=48' \
        -e 'SHARDS=1' \
        -e "SIZE=$size" \
        -e 'BATCH_SIZE=128' \
        -e 'ORIGIN=http://localhost:8080' \
        --network host \
        -t importer; then
        echo "Importer failed, printing latest Weaviate logs..."  
        exit 1
    fi
    echo "Import completed successfully"
}

run_updater() {
    local size=$1
    if ! docker run \
        -e 'DIMENSIONS=48' \
        -e 'SHARDS=1' \
        -e "SIZE=$size" \
        -e 'BATCH_SIZE=128' \
        -e 'ORIGIN=http://localhost:8080' \
        --network host \
        -t updater; then
        echo "Updater failed, printing latest Weaviate logs..."  
        exit 1
    fi
    echo "Updater completed successfully"
}

validate_tombstone_count() {
    local retries=$1
    local attempt=1
    local sleep_time=10
    
    while [ $attempt -le $retries ]; do
        local tombstones=$(curl -s localhost:2112/metrics \
            | grep vector_index_tombstones \
            | grep -v "^#" \
            | awk '{print $2}')

        if [ "$tombstones" -eq 0 ]; then
            echo "No tombstones to be cleaned up"
            return 0
        fi

        echo "Tombstones to be cleaned up, wanted 0, got $tombstones"
        echo "Retrying in $sleep_time seconds..."
        sleep $sleep_time
        attempt=$((attempt + 1))
    done

    echo "Failed to validate tombstone count after $retries attempts"
    return 1
}

validate_object_count() {
    local expected_count=$1
    local retries=$2
    local attempt=1
    local sleep_time=5

    while [ $attempt -le $retries ]; do
        local object_count=$(curl -s 'localhost:8080/v1/graphql' -X POST \
            -H 'content-type: application/json' \
            -d "{\"query\":\"{Aggregate{DemoClass{meta{count}}}}\"}" | \
            jq '.data.Aggregate.DemoClass[0].meta.count')

        if [ "$object_count" -ge "$expected_count" ]; then
            echo "Object count is correct"
            return 0
        fi

        echo "Not enough objects present, wanted $expected_count, got $object_count"
        echo "Retrying in $sleep_time seconds..."
        sleep $sleep_time
        attempt=$((attempt + 1))
    done

    echo "Failed to validate object count after $retries attempts"
    return 1
}

wait_for_hnsw_snapshot() {
    local retries=150
    local attempt=1
    
    while [ $attempt -le $retries ]; do
        if docker compose -f apps/weaviate/docker-compose.yml logs weaviate | grep "create_snapshot" | grep "started"; then
            echo "hnsw snapshot detected"
            return 0
        fi
        sleep 3
        attempt=$((attempt + 1))
    done

    echo "Failed to detect hnsw snapshot after $retries attempts"
    return 1
}

echo "Building all required containers"
( cd apps/importer/ && docker build -t importer . )
( cd apps/updater/ && docker build -t updater . )

export ASYNC_INDEXING=true
export COMPOSE="apps/weaviate/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate

echo "Run import script to import $SIZE objects"
run_importer $SIZE

echo "Wait for the hnsw snapshot to be created"
if ! wait_for_hnsw_snapshot; then
    exit 1
fi

echo "Restart the container"
docker compose -f apps/weaviate/docker-compose.yml kill weaviate \
    && docker compose -f apps/weaviate/docker-compose.yml up weaviate -d

wait_weaviate
echo "Wait some time to let the metrics be updated"
sleep 10

echo "Run updater script to add and delete objects after the crash"
#run_updater $SIZE

echo "Validate the count is correct"
if ! validate_object_count $SIZE 3; then
    exit 1
fi

echo "Passed!"
shutdown