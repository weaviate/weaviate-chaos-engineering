#!/bin/bash

set -e

SIZE=300000

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$1/v1/.well-known/ready; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready in port ${1} after 120s"
  exit 1
}

function shutdown() {
  echo "Cleaning up resources..."
  docker-compose -f apps/weaviate/docker-compose-replication.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker container rm -f importer &>/dev/null && echo 'Deleted container importer'
  docker container rm -f killer  &>/dev/null && echo 'Deleted container killer'
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Building all required containers"
( cd apps/replicated-import-sq/ && docker build -t importer . )
( cd apps/chaotic-cluster-killer/ && docker build -t killer . )

echo "Starting Weaviate version $WEAVIATE_VERSION ..."

docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo Current directory: $PWD

echo "Import schema"
if ! docker run \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  --rm \
  --name importer \
  -t importer python3 run.py --action schema; then
  echo "Could not apply schema"
  docker-compose -f apps/weaviate/docker-compose.yml logs
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
  -e "ENABLE_MODULES=\"text2vec-bigram,$ENABLE_MODULES\"" \
  -e "BIGRAM=\"trigram\"" \
  -e "ASYNC_INDEXING=\"true\"" \
  --network host \
  -t importer python3 run.py --action import; then
  echo "Importer failed, printing latest Weaviate logs..."
  docker-compose -f apps/weaviate/docker-compose-replication.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi

echo "Import completed successfully, stop killer"
docker rm -f killer
