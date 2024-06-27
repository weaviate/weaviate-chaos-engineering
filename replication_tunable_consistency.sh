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

echo "Building all required containers"
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=generator -t generator . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=importer -t importer . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=patcher -t patcher . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=updater -t updater . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=cluster_healthy -t cluster_healthy . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=cluster_one_node_down -t cluster_one_node_down . )
( cd apps/replication_tunable_consistency/ && docker build --build-arg APP_NAME=cluster_one_node_remaining -t cluster_one_node_remaining . )

function shutdown() {
  echo "Cleaning up ressources..."
  docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker container rm -f generator &>/dev/null && echo 'Deleted container generator'
  docker container rm -f importer &>/dev/null && echo 'Deleted container importer'
  docker container rm -f patcher &>/dev/null && echo 'Deleted container patcher'
  docker container rm -f cluster_one_node_down &>/dev/null && echo 'Deleted container cluster_one_node_down'
  docker container rm -f updater &>/dev/null && echo 'Deleted container updater'
  docker container rm -f cluster_one_node_remaining &>/dev/null && echo 'Deleted container cluster_one_node_remaining'
  docker container rm -f cluster_healthy &>/dev/null && echo 'Deleted container cluster_healthy'
  rm -rf workdir
}
trap 'shutdown; exit 1' SIGINT ERR

rm -rf workdir
mkdir workdir
touch workdir/data.json

docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name generator -t generator

echo "Done generating."

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

# POST objects with consistency level ALL
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name importer -t importer

# Read objects with consistency level ONE
if docker run --network host -v "$PWD/workdir/:/workdir/data" --name cluster_healthy -t cluster_healthy; then
  echo "All objects read with consistency level ONE".
else
  docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi

# PATCH objects with one node down, consistency level QUORUM
echo "Killing node 3"
docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml kill weaviate-node-3
sleep 10
if docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name patcher -t patcher; then
  echo "All objects patched with consistency level QUORUM with one node down".
else
  docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi

# Restart dead node, read objects with consistency level QUORUM
echo "Restart node 3"
docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml up -d weaviate-node-3
wait_weaviate 8082
if docker run --network host -v "$PWD/workdir/:/workdir/data" --name cluster_one_node_down -t cluster_one_node_down; then
  echo "All objects read with consistency level QUORUM after weaviate-node-3 restarted".
else
  docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi

# PUT objects with only one node remaining, consistency level ONE
echo "Killing node 2"
docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml kill weaviate-node-2
echo "Killing node 3"
docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml kill weaviate-node-3
sleep 10
if docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name updater -t updater; then
  echo "All objects updated with consistency level ONE with only weaviate-node-1 up".
else
  docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi

# Restart dead nodes, read objects with consistency level ALL
docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml up -d weaviate-node-2
wait_weaviate 8081
docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml up -d weaviate-node-3
wait_weaviate 8082
if docker run --network host -v "$PWD/workdir/:/workdir/data" --name cluster_one_node_remaining -t cluster_one_node_remaining; then
  echo "All objects read with consistency level ALL after weaviate-node-2 and weaviate-node-3 restarted".
else
  docker-compose -f apps/weaviate/docker-compose-replication_single_voter.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi

echo "Success!"
