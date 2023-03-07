#!/bin/bash

if [ -z "$WEAVIATE_VERSION" ]; then
  echo "Please set WEAVIATE_VERSION, e.g. to 1.18.0"
  exit 1
fi

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$1/v1/.well-known/ready; then
      echo "Weaviate is ready"
      break
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
}

echo "Building all required containers"
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=generator -t generator . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=importer -t importer . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=patcher -t patcher . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=updater -t updater . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=cluster_healthy -t cluster_healthy . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=cluster_one_node_down -t cluster_one_node_down . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=cluster_one_node_remaining -t cluster_one_node_remaining . )

rm -rf workdir
mkdir workdir
touch workdir/data.json

docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" -t generator

echo "Done generating."

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1
wait_weaviate 8080
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-2
wait_weaviate 8081
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-3
wait_weaviate 8082

# POST objects with consistency level ALL
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" -t importer

# Read objects with consistency level ONE
docker run --network host -v "$PWD/workdir/:/workdir/data" -t cluster_healthy

# PATCH objects with one node down, consistency level QUORUM
echo "Killing node 3"
docker-compose -f apps/weaviate/docker-compose-replication.yml kill weaviate-node-3
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" -t patcher

# Restart dead node, read objects with consistency level QUORUM
echo "Restart node 3"
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-3
wait_weaviate 8082
docker run --network host -v "$PWD/workdir/:/workdir/data" -t cluster_one_node_down

# PUT objects with only one node remaining, consistency level ONE
echo "Killing node 2"
docker-compose -f apps/weaviate/docker-compose-replication.yml kill weaviate-node-2
echo "Killing node 3"
docker-compose -f apps/weaviate/docker-compose-replication.yml kill weaviate-node-3
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" -t updater

# Restart dead nodes, read objects with consistency level ALL
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-2
wait_weaviate 8081
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-3
wait_weaviate 8082
docker run --network host -v "$PWD/workdir/:/workdir/data" -t cluster_one_node_remaining

echo "Success!"
