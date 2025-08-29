#!/bin/bash

set -e

source common.sh

SIZE=6000

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
    
    while [ $attempt -le $retries ]; do
        if docker compose -f apps/weaviate/docker-compose.yml logs weaviate | grep "start hnsw condensing"; then
            echo "Condensing begin detected"
            return 0
        fi
        sleep 3
        attempt=$((attempt + 1))
    done

    echo "Failed to detect condensing begin after $retries attempts"
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

wait_weaviate

echo "Run import script to import $SIZE objects"
run_importer $SIZE

for i in {1..10}; do
    echo "Wait for the condensing to be started"
    if ! wait_for_condensing; then
        exit 1
    fi

    echo "Restart the container"
    docker compose -f apps/weaviate/docker-compose.yml kill weaviate \
        && docker compose -f apps/weaviate/docker-compose.yml up weaviate -d

    wait_weaviate
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

sleep 60

echo "Passed!"
shutdown
``