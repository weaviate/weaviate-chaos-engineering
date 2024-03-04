#!/bin/bash

set -e

SIZE=600000

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready after 120s"
  exit 1
}

echo "Building all required containers"
( cd apps/bm25-corruption/ && docker build -t importer . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

function debug_logs() {
  docker-compose -f apps/weaviate/docker-compose.yml logs --tail 100
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
