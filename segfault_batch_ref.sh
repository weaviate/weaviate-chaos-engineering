#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080/v1/.well-known/ready; then
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
  docker container rm -f segfault_batch_ref &>/dev/null && echo 'Deleted container segfault_batch_ref'
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Building all required containers"
( cd apps/segfault-on-batch-ref/ && docker build -t segfault_batch_ref . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up -d
wait_weaviate

function dump_logs() {
  docker-compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml logs
}

trap 'dump_logs' ERR


echo "Initialize schema"
docker run --network host --name segfault_batch_ref --rm -t segfault_batch_ref python3 run.py -a schema

echo "Run import script designed to lead to races between compaction and batch ref inserts"
docker run --network host --name segfault_batch_ref --rm -t segfault_batch_ref python3 run.py -a import

echo "Passed!"
shutdown
