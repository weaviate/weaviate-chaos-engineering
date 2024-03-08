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
( cd apps/concurrent-tombstone-cleanup/ && docker build -t concurrent-tombstone-cleanup . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Run imports and batch deletes to force concurrent tombstone cleanup"
docker run --network host -t concurrent-tombstone-cleanup python3 run.py
