#!/bin/bash

set -e

source common.sh

SIZE=100000

echo "Building all required containers"
( cd apps/importer-no-vector-index/ && docker build -t importer-no-vector . )
( cd apps/chaotic-killer/ && docker build -t killer . )

export COMPOSE="apps/weaviate/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate

echo "Starting the chaos script to kill Weaviate periodically (in the background)"
docker run \
  --network host \
  --rm -d \
  -v "$PWD:$PWD" \
  -w "$PWD" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name killer \
  killer

echo "Run import script in foreground..."
if ! docker run \
  -e 'SHARDS=1' \
  -e "SIZE=$SIZE" \
  -e 'BATCH_SIZE=128' \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  -t importer-no-vector; then
  echo "Importer failed, printing latest Weaviate logs..."  
    exit 1
fi

echo "Import completed successfully, stop killer"
docker rm -f killer

echo "Wait for Weaviate to be ready again in case there was a kill recently"
wait_weaviate

echo "Validate the count is correct"
attempt=1
retries=3
while [ $attempt -le $retries ]; do
  object_count=$(curl -s 'localhost:8080/v1/graphql' --fail -X POST \
    -H 'content-type: application/json' \
    -d '{"query":"{Aggregate{NoVector{meta{count}}}}"}' | \
    jq '.data.Aggregate.NoVector[0].meta.count')

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

echo "Passed!"
shutdown
