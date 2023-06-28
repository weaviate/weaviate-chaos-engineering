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
( cd apps/multi-node-references && docker build -t ref-importer . )


# We are reusing the replication docker-compose for this, but there is nothing
# special about the infra, it's essentially just a 3-node cluster which is
# perfect for this test
echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1
wait_weaviate 8080
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-2
wait_weaviate 8081
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-3
wait_weaviate 8082

echo "Run import script in foreground..."
if ! docker run \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  -t ref-importer python3 run.py; then
  echo "Importer failed, printing latest Weaviate logs..."
  docker-compose -f apps/weaviate/docker-compose-replication.yml logs --tail 100
  exit 1
fi

echo "Check for error logs"
errors="$(docker compose -f apps/weaviate/docker-compose-replication.yml logs 2>&1 | grep memberlist | grep error | wc -l | tr -d '[:space:]')"
if (( $warnings > 0 )); then 
  docker compose -f apps/weaviate/docker-compose-replication.yml logs 2>&1 | grep memberlist | grep error
  echo "too many errors ($errors)" 
  exit 1
fi

echo "No warnings. Passed."

