#!/bin/bash

set -e

source common.sh

git submodule update --init --remote --recursive

export COMPOSE="apps/weaviate/docker-compose-replication-static.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080 120 weaviate-node-1
wait_weaviate 8081 120 weaviate-node-2
wait_weaviate 8082 120 weaviate-node-3

echo "Building all required containers"
( cd apps/multi-tenancy-activate-deactivate/ && docker build -t multi-tenancy-activate-deactivate . )

echo "Run script"
docker run --network host --name multi-tenancy-activate-deactivate -t multi-tenancy-activate-deactivate

echo "Success"
shutdown
