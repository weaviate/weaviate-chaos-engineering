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
( cd apps/add-new-compressed-records/ && docker build -t add-new-compressed-records . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Run imports, compress, and add new compressed records"
docker run --network host -t add-new-compressed-records python3 run.py

if docker-compose -f apps/weaviate/docker-compose.yml logs | grep -q 'panic'; then 
  echo "panic found"
  exit 1
fi
echo "Passed!"