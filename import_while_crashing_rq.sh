#!/bin/bash

set -e

source common.sh

SIZE=50000

echo "Building all required containers"
( cd apps/importer/ && docker build -t importer . )
( cd apps/chaotic-killer/ && docker build -t killer . )

export COMPOSE="apps/weaviate/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate 8080 120 weaviate

echo "Starting the chaos script to kill Weaviate periodically (in the background)"
docker run \
  --network host \
  --rm -t -d \
  -e 'SLEEP_START=1' \
  -e 'SLEEP_END=5' \
  -v "$PWD:$PWD" \
  -w "$PWD" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name killer \
  killer

echo "Run import script in foreground..."
if ! docker run \
  -e 'DIMENSIONS=48' \
  -e 'SHARDS=1' \
  -e "SIZE=$SIZE" \
  -e 'BATCH_SIZE=128' \
  -e 'ORIGIN=http://localhost:8080' \
  -e "RQ_ENABLED=true" \
  --network host \
  -t importer; then
  echo "Importer failed, printing latest Weaviate logs..."
  exit 1
fi

echo "Import completed successfully, stop killer"
docker rm -f killer

echo "Wait for Weaviate to be ready again in case there was a kill recently"
wait_weaviate 8080 120 weaviate

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
