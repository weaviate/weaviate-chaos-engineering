#!/bin/bash

set -e

source common.sh

SIZE=20000

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

wait_for_condensing() {
    local retries=150
    local attempt=1

    # Detect Weaviate version to determine which log pattern to look for.
    # In 1.38+, the old condensor was replaced by compactv2 which emits different log messages.
    local version
    version=$(curl -sf localhost:8080/v1/meta | jq -r '.version' | grep -oE '^[0-9]+\.[0-9]+')
    local version_major version_minor
    version_major=$(echo "$version" | cut -d. -f1)
    version_minor=$(echo "$version" | cut -d. -f2)

    local grep_pattern
    if [ "$version_major" -gt 1 ] || { [ "$version_major" -eq 1 ] && [ "$version_minor" -ge 38 ]; }; then
        grep_pattern="hnsw_compactor_merge|hnsw_compactor_snapshot|hnsw_compactor_convert"
        echo "Weaviate >= 1.38 detected (${version}), waiting for compactv2 activity"
    else
        grep_pattern="start hnsw condensing"
        echo "Weaviate < 1.38 detected (${version}), waiting for condensing"
    fi

    while [ $attempt -le $retries ]; do
        if docker compose -f apps/weaviate/docker-compose.yml logs weaviate | grep -E "$grep_pattern"; then
            echo "Compaction/condensing activity detected"
            return 0
        fi
        sleep 3
        attempt=$((attempt + 1))
    done

    echo "Failed to detect compaction/condensing after $retries attempts"
    return 1
}

validate_logs() {

    if docker compose -f apps/weaviate/docker-compose.yml logs weaviate | grep -iE "error:|panic:|runtime error:|goroutine.*\[running\]|panic@|unrecognized commit type"; then
        echo "Errors detected in logs"
        return 1
    fi

    echo "No errors detected in logs"
    return 0
}

echo "Building all required containers"
( cd apps/importer/ && docker build -t importer . )
( cd apps/updater/ && docker build -t updater . )

export ASYNC_INDEXING=true
export COMPOSE="apps/weaviate/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate 8080 120 weaviate

echo "Run import script to import $SIZE objects"
run_importer $SIZE

echo "Run updater script to facilitate condensing"
run_updater $SIZE

for i in {1..10}; do
    echo "Wait for the condensing to be started"
    if ! wait_for_condensing; then
        exit 1
    fi

    echo "Restart the container"
    docker compose -f apps/weaviate/docker-compose.yml kill weaviate \
        && docker compose -f apps/weaviate/docker-compose.yml up weaviate -d

    wait_weaviate 8080 120 weaviate
done

echo "Validate the count is correct"
if ! validate_object_count $SIZE 3; then
    sleep 60
    exit 1
fi

echo "Wait some time to let the condensing be completed"
sleep 50

echo "Validate logs"
if ! validate_logs; then
    exit 1
fi

echo "Passed!"
shutdown
``