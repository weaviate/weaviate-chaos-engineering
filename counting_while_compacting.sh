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
