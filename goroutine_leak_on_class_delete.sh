#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/goroutine-leak-on-class-delete/ && docker build -t goroutine-test-script . )

# We are reusing the replication docker compose for this, but there is nothing
# special about the infra, it's essentially just a 3-node cluster which is
# perfect for this test
echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-cpu-constrained.yml up -d
wait_weaviate 8080

echo "Run test script"
docker run --network host -t goroutine-test-script python3 run.py

echo "Passed!"
shutdown
