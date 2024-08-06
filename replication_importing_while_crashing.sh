#!/bin/bash

set -e

source common.sh

SIZE=300000

echo "Building all required containers"
( cd apps/replicated-import/ && docker build -t importer . )
( cd apps/chaotic-cluster-killer/ && docker build -t killer . )

echo "Starting Weaviate..."

docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Import schema"
if ! docker run \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  --rm \
  --name importer \
  -t importer python3 run.py --action schema; then
  echo "Could not apply schema"
  docker compose -f apps/weaviate/docker-compose.yml logs
  exit 1
fi

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
  -t importer python3 run.py --action import; then
  echo "Importer failed, printing latest Weaviate logs..."
  docker compose -f apps/weaviate/docker-compose-replication.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi

echo "Import completed successfully, stop killer"
docker rm -f killer
echo "Passed!"
shutdown
