#!/bin/bash

set -e

source common.sh

SIZE=1000000

wait_for_quantization_started() {
    local retries=150
    local attempt=1

    while [ $attempt -le $retries ]; do
        if docker compose -f $COMPOSE logs weaviate | grep -i "switching to compressed vectors"; then
            echo "Quantization started detected"
            return 0
        fi
        sleep 3
        attempt=$((attempt + 1))
    done

    echo "Failed to detect quantization start after $retries attempts"
    return 1
}

echo "Building all required containers"
( cd apps/importer/ && docker build -t importer . )

export COMPOSE="apps/weaviate/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate 8080 120 weaviate

echo "Run import script in background so we can watch for quantization..."
importer_id=$(docker run \
  -e 'DIMENSIONS=48' \
  -e 'SHARDS=1' \
  -e "SIZE=$SIZE" \
  -e 'BATCH_SIZE=128' \
  -e 'ORIGIN=http://localhost:8080' \
  -e "RQ_ENABLED=true" \
  --network host \
  -d importer)
echo "Importer container: $importer_id"

echo "Waiting for quantization to start..."
if ! wait_for_quantization_started; then
    docker rm -f "$importer_id" || true
    exit 1
fi

echo "Killing Weaviate during quantization"
docker compose -f $COMPOSE kill weaviate
docker compose -f $COMPOSE up weaviate -d

wait_weaviate 8080 120 weaviate

# Wait for the importer to complete
docker wait "$importer_id"

echo "Wait for async indexing queue to drain..."
wait_for_indexing "http://localhost:8080" 300

echo "Validate the count is correct"
attempt=1
retries=3
while [ $attempt -le $retries ]; do
  object_count=$(curl -s 'localhost:8080/v1/graphql' -X POST \
    -H 'content-type: application/json' \
    -d '{"query":"{Aggregate{DemoClass{meta{count}}}}"}' | \
    jq '.data.Aggregate.DemoClass[0].meta.count')

  if [ "$object_count" -ge "$SIZE" ]; then
    echo "Object count is correct"
    break
  fi

  echo "Not enough objects present, wanted $SIZE, got $object_count"
  echo "Retrying in 5 seconds..."
  sleep 5
  attempt=$((attempt + 1))
done

if [ $attempt -gt $retries ]; then
  echo "Failed to validate object count after $retries attempts"
  exit 1
fi

echo "Validate RQ compression via debug stats"
nodes_response=$(curl -s 'localhost:8080/v1/nodes?output=verbose')

# Find the shard that has compressed=true
shard_name=$(echo "$nodes_response" | jq -r '.nodes[].shards[] | select(.compressed == true) | .name' | head -1)
class_name=$(echo "$nodes_response" | jq -r '.nodes[].shards[] | select(.compressed == true) | .class' | head -1)

if [ -z "$shard_name" ] || [ "$shard_name" = "null" ]; then
  echo "ERROR: No compressed shard found"
  echo "Nodes response: $nodes_response"
  exit 1
fi

echo "Found compressed shard: class=$class_name shard=$shard_name"

debug_response=$(curl -s "localhost:6060/debug/stats/collection/$class_name/shards/$shard_name")
cache_size=$(echo "$debug_response" | jq '.cacheSize')
compression_type=$(echo "$debug_response" | jq -r '.compressionType')

echo "Compression type: $compression_type"
echo "Cache size: $cache_size"
echo "Import size: $SIZE"

if [ "$compression_type" != "rq" ]; then
  echo "ERROR: Expected compression type 'rq', got '$compression_type'"
  exit 1
fi

if [ "$cache_size" -lt "$SIZE" ]; then
  echo "ERROR: Cache size ($cache_size) is less than import size ($SIZE)"
  exit 1
fi

echo "RQ validation passed: compressionType=rq, cacheSize=$cache_size >= $SIZE"

echo "Passed!"
shutdown
