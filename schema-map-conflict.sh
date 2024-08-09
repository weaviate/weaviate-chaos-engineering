#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/schema-map-conflict/ && docker build --build-arg APP_NAME=tenant_creator -t tenant_creator . )
( cd apps/schema-map-conflict/ && docker build --build-arg APP_NAME=tenant_updater -t tenant_updater . )
( cd apps/schema-map-conflict/ && docker build --build-arg APP_NAME=tenant_deleter -t tenant_deleter . )

rm -rf workdir
mkdir workdir
touch workdir/data.json

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Killing node 3"
docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml kill weaviate-node-3


# Create multi tenant classes while the node is down
docker run  --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name tenant_creator -t tenant_creator

# Start updating tenants in the background
# Create multi tenant classes while the node is down
docker run -d --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name tenant_updater -t tenant_updater

# Create multi tenant classes while the node is down
docker run -d --network host -v "$PWD/workdir/data.json:/workdir/data.json" --name tenant_deleter -t tenant_deleter 

# Restart dead node, read objects from Node 3 while tenants are being deleted
echo "Restart node 3"
docker compose -f apps/weaviate/docker-compose-replication_single_voter.yml up -d weaviate-node-3
wait_weaviate 8082


echo "Success!"
shutdown
