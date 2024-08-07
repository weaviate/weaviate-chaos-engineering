#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=generator -t generator . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=regenerator -t regenerator . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=importer -t importer . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=reimporter -t reimporter . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=deleter -t deleter . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=cluster_healthy -t cluster_healthy . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=check_objects_in_nodes -t check_objects_in_nodes . )
( cd apps/deletes_with_node_out_of_sync/ && docker build --build-arg APP_NAME=check_objects_deleted -t check_objects_deleted . )

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
    exit 1
fi

if docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name deleter -t deleter; then
  echo "All tenant2 objects deleted with consistency level ONE."
else
    exit 1
fi

if docker run --network host -v "$PWD/workdir/:/workdir/data" --name check_objects_deleted -t check_objects_deleted; then
  echo "tenant2 objects were deleted from all nodes."
else
    exit 1
fi

echo "Success!"
shutdown
