#!/bin/bash

set -e

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

echo "Building all required containers"
( cd apps/oom-prevention/ && docker build -t importer . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose-memory-constrained.yml up -d weaviate-node-1
wait_weaviate 8080
docker-compose -f apps/weaviate/docker-compose-memory-constrained.yml up -d weaviate-node-2
wait_weaviate 8081
docker-compose -f apps/weaviate/docker-compose-memory-constrained.yml up -d weaviate-node-3
wait_weaviate 8082

echo "Run import script in foreground..."
if ! docker run \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  -t importer python3 run.py; then
  echo "Importer failed, printing latest Weaviate logs..."
  echo "Node 1:"
  docker-compose -f apps/weaviate/docker-compose-memory-constrained.yml logs weaviate-node-1
  echo "Node 2:"
  docker-compose -f apps/weaviate/docker-compose-memory-constrained.yml logs weaviate-node-2
  echo "Node 3:"
  docker-compose -f apps/weaviate/docker-compose-memory-constrained.yml logs weaviate-node-3
  docker-compose -f apps/weaviate/docker-compose-memory-constrained.yml ps
  docker stats

  exit 1
fi

echo "Passed!"
