#!/bin/bash

set -e

source common.sh

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

docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name generator -t generator

echo "Done generating."

export COMPOSE="apps/weaviate/docker-compose-replication_single_low_network_pressure.yml"

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

# PATCH objects with one node down, consistency level QUORUM
echo "Killing node 3"
docker compose -f $COMPOSE kill weaviate-node-3
sleep 10
if docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name patcher -t patcher; then
  echo "All objects patched with consistency level QUORUM with one node down".
else
  exit 1
fi

# Restart dead node, read objects with consistency level QUORUM
echo "Restart node 3"
docker compose -f $COMPOSE up -d   weaviate-node-3
wait_weaviate 8082
if docker run --network host -v "$PWD/workdir/:/workdir/data" --name cluster_one_node_down -t cluster_one_node_down; then
  echo "All objects read with consistency level QUORUM after weaviate-node-3 restarted".
else
  exit 1
fi

# PUT objects with only one node remaining, consistency level ONE
echo "Killing node 2"
docker compose -f $COMPOSE kill weaviate-node-2
echo "Killing node 3"
docker compose -f $COMPOSE kill weaviate-node-3
sleep 10
if docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name updater -t updater; then
  echo "All objects updated with consistency level ONE with only weaviate-node-1 up".
else
  exit 1
fi

# Restart dead nodes, read objects with consistency level ALL
docker compose -f $COMPOSE up -d  weaviate-node-2
wait_weaviate 8081
# sleep to avoid any races in joining the cluster
sleep 3
docker compose -f $COMPOSE up -d  weaviate-node-3
wait_weaviate 8082
if docker run --network host -v "$PWD/workdir/:/workdir/data" --name cluster_one_node_remaining -t cluster_one_node_remaining; then
  echo "All objects read with consistency level ALL after weaviate-node-2 and weaviate-node-3 restarted".
else
  exit 1
fi

echo "Success!"
shutdown
