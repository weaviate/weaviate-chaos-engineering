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

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml up -d
for i in $(seq 1 30); do
    docker logs weaviate_weaviate_1
    sleep 1
end
echo "Passed"
