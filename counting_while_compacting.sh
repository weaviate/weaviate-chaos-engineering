#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/counting-while-compacting/ && docker build -t counting-while-compacting . )

echo "Starting Weaviate..."
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up -d

wait_weaviate

function dump_logs() {
  docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml logs
  docker ps -a
}

trap 'dump_logs' ERR


echo "Run import script that imports, deletes and counts objects"
docker run --network host -e ORIGIN=http://localhost:8080 -t counting-while-compacting

echo "Passed!"
shutdown
