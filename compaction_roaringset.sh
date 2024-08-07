#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/compaction-roaringset/ && docker build -t compaction-roaringset . )

echo "Starting Weaviate..."
PERSISTENCE_MEMTABLES_FLUSH_IDLE_AFTER_SECONDS=1 docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up -d

wait_weaviate

function dump_logs() {
  docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml logs
}

trap 'dump_logs' ERR

echo "Run create/delete objects script designed to panic on compacting roaringsets"
docker run --network host -t compaction-roaringset python3 run.py

echo "Passed!"
shutdown
