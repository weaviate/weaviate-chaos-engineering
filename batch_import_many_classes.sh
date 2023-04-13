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
( cd apps/batch-import-many-classes/ && docker build -t batch_import_many_classes . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Run expensive import in the background"
docker run -d --network host -t batch_import_many_classes python3 expensive_batches.py

echo "Run class creation and deletion in the foreground - fail on timeout"
docker run --network host -t batch_import_many_classes python3 many_classes.py

echo "Passed!"
