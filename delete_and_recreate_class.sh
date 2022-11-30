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
( cd apps/delete_and_recreate && docker build -t delete_and_recreate . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Run consecutive create and update operations"
docker run --network host -t delete_and_recreate python3 delete_and_recreate.py

echo "Passed!"
