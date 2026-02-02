#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/compaction-and-cleanup/ && docker build -t compaction-and-cleanup . )

export COMPOSE="apps/weaviate-no-restart-on-crash/docker-compose.yml"

echo "Starting Weaviate..."
PERSISTENCE_LSM_MAX_SEGMENT_SIZE=20MiB PERSISTENCE_MEMTABLES_MAX_SIZE_MB=6 PERSISTENCE_MEMTABLES_FLUSH_DIRTY_AFTER_SECONDS=1 PERSISTENCE_LSM_SEGMENTS_CLEANUP_INTERVAL_HOURS=1 docker compose -f $COMPOSE up -d

wait_weaviate 8080 120 weaviate

function dump_logs() {
  docker compose -f $COMPOSE logs
  docker ps -a
}

trap 'dump_logs' ERR

echo "Run script that imports, deletes, updates and counts objects during compactions and cleanups"
docker run --network host -e ORIGIN=http://localhost:8080 -t compaction-and-cleanup

echo "Passed!"
shutdown
