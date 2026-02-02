#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/compaction-roaringset/ && docker build -t compaction-roaringset . )

export COMPOSE="apps/weaviate-no-restart-on-crash/docker-compose.yml"
echo "Starting Weaviate..."
PERSISTENCE_MEMTABLES_FLUSH_IDLE_AFTER_SECONDS=1 docker compose -f $COMPOSE up -d

wait_weaviate 8080 120 weaviate

echo "Run create/delete objects script designed to panic on compacting roaringsets"
docker run --network host -t compaction-roaringset python3 run.py

echo "Passed!"
shutdown
