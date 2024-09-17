#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/segfault-on-filtered-vector-search/ && docker build -t segfault_filtered_vector_search . )

export COMPOSE="apps/weaviate-no-restart-on-crash/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate

function dump_logs() {
  docker compose -f $COMPOSE logs
}

trap 'dump_logs' ERR


echo "Initialize schema"
docker run --network host --rm --name segfault_filtered_vector_search -t segfault_filtered_vector_search python3 run.py -a schema

echo "Run multiple query scripts in the background"
for i in {1..3}; do
  docker run -d --network host --rm --name "segfault_filtered_vector_search-$i" -t segfault_filtered_vector_search python3 run.py -a query
done

echo "Run import script designed to lead to frequent hash prop compactions"
docker run --network host --rm --name segfault_filtered_vector_search -t segfault_filtered_vector_search python3 run.py -a import

echo "Passed!"
shutdown
