#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/async_repair/ && docker build --build-arg APP_NAME=generator -t generator . )
( cd apps/async_repair/ && docker build --build-arg APP_NAME=importer -t importer . )
( cd apps/async_repair/ && docker build --build-arg APP_NAME=importer_additional -t importer_additional . )
( cd apps/async_repair/ && docker build --build-arg APP_NAME=cluster_healthy -t cluster_healthy . )
( cd apps/async_repair/ && docker build --build-arg APP_NAME=cluster_async_repair -t cluster_async_repair . )


rm -rf workdir
mkdir workdir
touch workdir/data.json

docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name generator -t generator

echo "Done generating."

export COMPOSE="apps/weaviate/docker-compose-replication_single_voter.yml"
echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

# POST objects with consistency level ALL
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name importer -t importer

# Read objects with consistency level ONE
if docker run --network host -v "$PWD/workdir/:/workdir/data" --name cluster_healthy -t cluster_healthy; then
  echo "All objects read with consistency level ONE".
else
  exit 1
fi

# ADD objects with one node down, consistency level QUORUM
echo "Killing node 3"
docker compose -f $COMPOSE kill weaviate-node-3
sleep 10
if docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name importer_additional -t importer_additional; then
  echo "All objects added with consistency level QUORUM with one node down".
else
  exit 1
fi

# Restart dead node, read objects with consistency level ALL
echo "Restart node 3"
docker compose -f $COMPOSE up -d weaviate-node-3
wait_weaviate 8082
# Give some time for async repair to restore the objects in the restarted node
sleep 5
if docker run --network host -v "$PWD/workdir/:/workdir/data" --name cluster_async_repair -t cluster_async_repair; then
  echo "All objects read with consistency level ALL after weaviate-node-3 restarted".
else
  exit 1
fi

echo "Success!"
