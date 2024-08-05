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
( cd apps/geo-crash/ && docker build -t geo_crash . )

echo "Starting Weaviate..."
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up -d

wait_weaviate

echo "Import geo props with many duplicates (lots of hnsw commit logging)"
docker run --network host -t geo_crash python3 run.py

echo "Passed!"
