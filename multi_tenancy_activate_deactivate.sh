#!/bin/bash

set -e

git submodule update --init --recursive

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$1; then
      echo "Weaviate is ready"
      break
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
}

# We are reusing the replication docker compose for this, but there is nothing
# special about the infra, it's essentially just a 3-node cluster which is
# perfect for this test
echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1
wait_weaviate 8080
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-2
wait_weaviate 8081
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-3
wait_weaviate 8082

echo "Building all required containers"
( cd apps/multi-tenancy-activate-deactivate/ && docker build -t multi-tenancy-activate-deactivate . )

echo "Run script"
docker run --network host -t multi-tenancy-activate-deactivate