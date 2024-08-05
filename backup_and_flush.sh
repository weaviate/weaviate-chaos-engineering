#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080/v1/.well-known/ready; then
      echo "Weaviate is ready"
      break
    fi

    echo "Weaviate is not ready, trying again in 1s"
    sleep 1
  done
}

echo "Building all required containers"
( cd apps/backup-and-flush/ && docker build -t backup_and_flush . )

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Perform a backup and verify that flushing is restablished after the backup is finished."
docker run --network host -v ./apps/weaviate/data:/data -t backup_and_flush python3 backup_and_flush.py

echo "Passed!"
