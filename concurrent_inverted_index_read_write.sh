#!/bin/bash

set -e

source common.sh

SIZE=100000 

echo "Building all required containers"
( cd apps/importer-concurrent-inverted-index/ && docker build -t importer . )

export COMPOSE="apps/weaviate/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate


echo "Run import script in foreground..."
if ! docker run \
  -e 'DIMENSIONS=48' \
  -e 'SHARDS=1' \
  -e "SIZE=$SIZE" \
  -e 'BATCH_SIZE=128' \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  -t importer; then
  echo "Importer failed, printing latest Weaviate logs..."  
  exit 1
fi

echo "Passed!"
shutdown
