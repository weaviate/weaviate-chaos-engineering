#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$1; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready in port ${1} after 120s"
  exit 1
}

echo "Building all required containers"
( cd apps/goroutine-leak-on-class-delete/ && docker build -t goroutine-test-script . )

# We are reusing the replication docker compose for this, but there is nothing
# special about the infra, it's essentially just a 3-node cluster which is
# perfect for this test
echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-cpu-constrained.yml up -d
wait_weaviate 8080

echo "Run test script"
docker run --network host -t goroutine-test-script python3 run.py
