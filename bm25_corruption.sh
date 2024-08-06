#!/bin/bash

set -e

source common.sh

SIZE=600000 

echo "Building all required containers"
( cd apps/bm25-corruption/ && docker build -t importer . )

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

function debug_logs() {
  docker compose -f apps/weaviate/docker-compose.yml logs --tail 100
}
trap debug_logs ERR

echo "Starting importing and killing"
docker run \
  --network host \
  --rm -t \
  -v "$PWD:$PWD" \
  -w "$PWD" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name importer \
  importer sh -c "python3 /app/run.py --mode=docker"

echo "Passed!"
shutdown
