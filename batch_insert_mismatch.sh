#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/batch-insert-mismatch/ && docker build -t batch-insert-mismatch . )

export COMPOSE="apps/weaviate/docker-compose-c11y.yml"
echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate 8080 120 weaviate

echo "Run consecutive create and delete operations"
docker run --network host -t batch-insert-mismatch python3 batch-insert-mismatch.py

echo "Passed!"
shutdown
