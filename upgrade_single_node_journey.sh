#!/bin/bash

set -e

source common.sh

export COMPOSE="apps/weaviate/docker-compose-single-voter-without-node-name.yml"

function restart() {
  echo "Restarting node ..."
  docker compose -f $COMPOSE kill weaviate-node-1
  docker compose -f $COMPOSE up -d --force-recreate  weaviate-node-1
  wait_weaviate 8080 120 weaviate-node-1
}

function validateObjects() {
  echo "Validate objects ..."
  ( docker container rm -f cluster_healthy &>/dev/null )
  ( cd apps/upgrade-single-node/ && docker build --build-arg APP_NAME=cluster_healthy -t cluster_healthy . || true )
  if docker run --network host -v "$PWD/workdir/:/workdir/data" --name cluster_healthy -t cluster_healthy; then
    echo "All objects read with consistency level ONE".
  else
    docker compose -f $COMPOSE logs weaviate-node-1
    exit 1
  fi
}

echo "Building all required containers"
( cd apps/upgrade-single-node/ && docker build --build-arg APP_NAME=generator -t generator . )
( cd apps/upgrade-single-node/ && docker build --build-arg APP_NAME=importer -t importer . )
( cd apps/upgrade-single-node/ && docker build --build-arg APP_NAME=importer_additional -t importer_additional . )

sudo rm -rf apps/weaviate/data* || true  
sudo rm -rf workdir || true
mkdir workdir
touch workdir/data.json

docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name generator -t generator

echo "Done generating."

echo "Starting Weaviate 1.25..."
export WEAVIATE_NODE_VERSION=1.25.0
docker compose -f $COMPOSE up -d weaviate-node-1
wait_weaviate 8080 120 weaviate-node-1

# POST objects with consistency level ONE
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name importer -t importer

# Read objects with consistency level ONE
validateObjects

echo "Upgrade Weaviate..."
export WEAVIATE_NODE_VERSION=$WEAVIATE_VERSION
docker compose -f $COMPOSE up -d --force-recreate  weaviate-node-1
wait_weaviate 8080 120 weaviate-node-1


# Read objects with consistency level ONE
validateObjects

restart

echo "Import additional objects"
if docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name importer_additional -t importer_additional; then
  echo "All objects added with consistency level QUORUM with one node down".
else
  exit 1
fi

validateObjects

restart

validateObjects

echo "Success!"
shutdown
