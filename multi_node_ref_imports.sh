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

function shutdown() {
  echo "Cleaning up ressources..."
  docker-compose -f apps/weaviate/docker-compose-replication.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker container rm -f ref-importer &>/dev/null && echo 'Deleted container ref-importer'
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Building all required containers"
( cd apps/multi-node-references && docker build -t ref-importer . )

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Run import script in foreground..."
if ! docker run \
  -e 'ORIGIN=http://localhost:8080' \
  --rm \
  --name ref-importer \
  --network host \
  -t ref-importer python3 run.py; then
  echo "Importer failed, printing latest Weaviate logs..."
  docker-compose -f apps/weaviate/docker-compose-replication.yml logs --tail 100
  shutdown
  exit 1
fi

echo "Check for error logs"
errors="$(docker compose -f apps/weaviate/docker-compose-replication.yml logs 2>&1 | grep memberlist | grep error | wc -l | tr -d '[:space:]')"
if (( $warnings > 0 )); then
  docker compose -f apps/weaviate/docker-compose-replication.yml logs 2>&1 | grep memberlist | grep error
  echo "too many errors ($errors)"
  shutdown
  exit 1
fi

echo "No errors. Passed."
shutdown
