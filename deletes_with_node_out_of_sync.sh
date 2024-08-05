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
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=generator -t generator . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=regenerator -t regenerator . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=importer -t importer . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=reimporter -t reimporter . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=deleter -t deleter . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=cluster_healthy -t cluster_healthy . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=check_objects_in_nodes -t check_objects_in_nodes . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=check_objects_deleted -t check_objects_deleted . )

function shutdown() {
  echo "Cleaning up ressources..."
  docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker container rm -f generator &>/dev/null && echo 'Deleted container generator'
  docker container rm -f regenerator &>/dev/null && echo 'Deleted container regenerator'
  docker container rm -f importer &>/dev/null && echo 'Deleted container importer'
  docker container rm -f reimporter &>/dev/null && echo 'Deleted container reimporter'
  docker container rm -f deleter &>/dev/null && echo 'Deleted container deleter'
  docker container rm -f check_objects_in_nodes &>/dev/null && echo 'Deleted container check_objects_in_nodes'
  docker container rm -f check_objects_deleted &>/dev/null && echo 'Deleted container check_objects_deleted'
  docker container rm -f cluster_healthy &>/dev/null && echo 'Deleted container cluster_healthy'
  rm -rf workdir
}
trap 'shutdown; exit 1' SIGINT ERR

rm -rf workdir
mkdir workdir
touch workdir/data.json

# Genearate objects for tenant1
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name generator -t generator


echo "Done generating."

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

# Import tenant1 objects with consistency level ALL
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name importer -t importer

# Read objects for tenant1 with consistency level ONE
if docker run --network host -v "$PWD/workdir/:/workdir/data" --name cluster_healthy -t cluster_healthy; then
  echo "All objects read with consistency level ONE".
else
  docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi

# Genearate objects for tenant2 
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name regenerator -t regenerator

echo "Killing node 3"
docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml kill weaviate-node-3
sleep 10
# Import tenant2 objects with one node down, consistency level QUORUM
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name reimporter -t reimporter

# Restart dead node, read objects from Node 3 with consistency level ONE
echo "Restart node 3"
docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml up -d weaviate-node-3
wait_weaviate 8082

if docker run --network host -v "$PWD/workdir/:/workdir/data" --name check_objects_in_nodes -t check_objects_in_nodes; then
  echo "tenant2 objects are present in Node 1 but not in Node 3, as it was down."
else
  docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi

if docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name deleter -t deleter; then
  echo "All tenant2 objects deleted with consistency level ONE."
else
  docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi

if docker run --network host -v "$PWD/workdir/:/workdir/data" --name check_objects_deleted -t check_objects_deleted; then
  echo "tenant2 objects were deleted from all nodes."
else
  docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  exit 1
fi
