#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080; then
      echo "Weaviate is ready"
      break
    fi

    echo "Weaviate is not ready, trying again in 1s"
    sleep 1
  done
}

echo "Building all required containers"
( cd apps/counting-while-compacting/ && docker build -t counting-while-compacting . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up -d

wait_weaviate

function dump_logs() {
  docker-compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml logs
}

trap 'dump_logs' ERR


echo "Run import script that imports, deletes and counts objects"
docker run --network host -e ORIGIN=http://localhost:8080 -it counting-while-compacting

echo "Passed!"
