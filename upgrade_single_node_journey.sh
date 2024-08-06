#!/bin/bash

set -e

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
  echo "Cleaning up ressources..."
  docker compose -f apps/weaviate/docker-compose-single-voter-without-node-name.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true  
  docker container rm -f generator &>/dev/null && echo 'Deleted container generator'
  docker container rm -f importer &>/dev/null && echo 'Deleted container importer'
  docker container rm -f importer_additional &>/dev/null && echo 'Deleted container importer_additional'
  docker container rm -f cluster_read_repair &>/dev/null && echo 'Deleted container cluster_read_repair'
  docker container rm -f cluster_healthy &>/dev/null && echo 'Deleted container cluster_healthy'
  rm -rf workdir
}
trap 'shutdown; exit 1' SIGINT ERR


function restart() {
  echo "Restarting node ..."
  docker compose -f apps/weaviate/docker-compose-single-voter-without-node-name.yml kill weaviate-node-1
  docker compose -f apps/weaviate/docker-compose-single-voter-without-node-name.yml up -d --force-recreate  weaviate-node-1
  wait_weaviate 8080
}

function validateObjects() {
  echo "Validate objects ..."
  ( docker container rm -f cluster_healthy &>/dev/null )
  ( cd apps/upgrade-single-node/ && docker build --build-arg APP_NAME=cluster_healthy -t cluster_healthy . || true )
  if docker run --network host -v "$PWD/workdir/:/workdir/data" --name cluster_healthy -t cluster_healthy; then
    echo "All objects read with consistency level ONE".
  else
    docker compose -f apps/weaviate/docker-compose-single-voter-without-node-name.yml logs weaviate-node-1 
    exit 1
  fi
}

echo "Building all required containers"
( cd apps/upgrade-single-node/ && docker build --build-arg APP_NAME=generator -t generator . )
( cd apps/upgrade-single-node/ && docker build --build-arg APP_NAME=importer -t importer . )
( cd apps/upgrade-single-node/ && docker build --build-arg APP_NAME=importer_additional -t importer_additional . )

rm -rf apps/weaviate/data* || true  
rm -rf workdir
mkdir workdir
touch workdir/data.json

docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name generator -t generator

echo "Done generating."

echo "Starting Weaviate 1.25..."
export WEAVIATE_NODE_VERSION=1.25.0
docker compose -f apps/weaviate/docker-compose-single-voter-without-node-name.yml up -d weaviate-node-1
wait_weaviate 8080

# POST objects with consistency level ONE
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name importer -t importer

# Read objects with consistency level ONE
validateObjects

echo "Upgrade Weaviate..."
export WEAVIATE_NODE_VERSION=$WEAVIATE_VERSION
docker compose -f apps/weaviate/docker-compose-single-voter-without-node-name.yml up -d --force-recreate  weaviate-node-1
wait_weaviate 8080


# Read objects with consistency level ONE
validateObjects

restart

echo "Import additional objects"
if docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name importer_additional -t importer_additional; then
  echo "All objects added with consistency level ONE".
else
  docker compose -f apps/weaviate/docker-compose-single-voter-without-node-name.yml logs weaviate-node-1
  exit 1
fi

validateObjects

restart

validateObjects

echo "Success!"
shutdown
