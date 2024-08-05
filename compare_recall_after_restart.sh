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
( cd apps/recall/ && docker build -t recall . )
( cd apps/recall-check/ && docker build -t recall-checker . )

rm -rf workdir
mkdir workdir
touch workdir/data.json

echo "Generate a dataset of 100k objects"
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" -t recall python3 generate.py

echo "Done generating."

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Import into Weaviate..."
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" -t recall python3 import.py

echo "Check Recall"
docker run --network host -v "$PWD/workdir/:/app/data" -t recall-checker

echo "Restart Weaviate"
docker compose -f apps/weaviate/docker-compose.yml stop weaviate && \
  docker compose -f apps/weaviate/docker-compose.yml start weaviate

wait_weaviate

echo "Check Recall again"
docker run --network host -v "$PWD/workdir/:/app/data" -t recall-checker
