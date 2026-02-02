#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/goroutine-leak-on-class-delete/ && docker build -t goroutine-test-script . )

export COMPOSE="apps/weaviate/docker-compose-cpu-constrained.yml"

# We are reusing the replication docker compose for this, but there is nothing
# special about the infra, it's essentially just a 3-node cluster which is
# perfect for this test
echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d
wait_weaviate 8080 120 weaviate

echo "Run test script"
docker run --network host -t goroutine-test-script python3 run.py

echo "Passed!"
shutdown
