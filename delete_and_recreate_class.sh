#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/delete_and_recreate && docker build -t delete_and_recreate . )

export COMPOSE="apps/weaviate/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate 8080 120 weaviate

echo "Run consecutive create and update operations"
docker run --network host -t delete_and_recreate python3 delete_and_recreate.py

echo "Passed!"
shutdown
