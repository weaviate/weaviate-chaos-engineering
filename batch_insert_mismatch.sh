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
( cd apps/batch-insert-mismatch/ && docker build -t batch-insert-mismatch . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose-c11y.yml up -d

wait_weaviate

echo "Run consecutive create and delete operations"
docker run --network host -t batch-insert-mismatch python3 batch-insert-mismatch.py

echo "Passed!"
