#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$1; then
      echo "Weaviate is ready"
      break
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
}

echo "Building all required containers"
( cd apps/tenants-crash-safety/ && docker build -t importer . )
( cd apps/chaotic-cluster-killer/ && docker build -t killer . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1
wait_weaviate 8080
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-2
wait_weaviate 8081
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-3
wait_weaviate 8082

echo "Starting the chaos script to kill Weaviate periodically (in the background)"
docker run \
  --network host \
  --rm -t -d \
  -e "WEAVIATE_VERSION=$WEAVIATE_VERSION" \
  -v "$PWD:$PWD" \
  -w "$PWD" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name killer \
  killer

echo "Run import script in foreground..."
if ! docker run \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  -t importer python3 import.py; then
  echo "Importer failed, printing latest Weaviate logs..."
  docker-compose -f apps/weaviate/docker-compose.yml logs 
  exit 1
fi

echo "Import completed successfully, stop killer"
docker rm -f killer

echo "Passed!"
