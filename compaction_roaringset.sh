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

echo "Building all required containers"
( cd apps/compaction-roaringset/ && docker build -t compaction-roaringset . )

echo "Starting Weaviate..."
PERSISTENCE_MEMTABLES_FLUSH_IDLE_AFTER_SECONDS=1 docker-compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up -d

wait_weaviate

function dump_logs() {
  docker-compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml logs
}

trap 'dump_logs' ERR

echo "Run create/delete objects script designed to panic on compacting roaringsets"
docker run --network host -t compaction-roaringset python3 run.py

echo "Passed!"
