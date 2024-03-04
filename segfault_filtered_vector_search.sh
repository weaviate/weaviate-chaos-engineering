#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready after 120s"
  exit 1
}

function shutdown() {
  echo "Cleaning up ressources..."
  docker-compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker container rm -f segfault_filtered_vector_search &>/dev/null && echo 'Deleted container segfault_filtered_vector_search'
  for i in {1..3}; do
    docker container rm -f "segfault_filtered_vector_search-$i" &>/dev/null && echo "Deleted container segfault_filtered_vector_search-$i"
  done
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Building all required containers"
( cd apps/segfault-on-filtered-vector-search/ && docker build -t segfault_filtered_vector_search . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up -d

wait_weaviate

function dump_logs() {
  docker-compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml logs
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
