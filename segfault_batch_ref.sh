#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/segfault-on-batch-ref/ && docker build -t segfault_batch_ref . )

export COMPOSE="apps/weaviate-no-restart-on-crash/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d
wait_weaviate 8080 120 weaviate

function dump_logs() {
  docker compose -f $COMPOSE logs
}

trap 'dump_logs' ERR


echo "Initialize schema"
docker run --network host --name segfault_batch_ref --rm -t segfault_batch_ref python3 run.py -a schema

echo "Run import script designed to lead to races between compaction and batch ref inserts"
docker run --network host --name segfault_batch_ref --rm -t segfault_batch_ref python3 run.py -a import

echo "Passed!"
shutdown
