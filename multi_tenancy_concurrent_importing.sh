#!/bin/bash

set -e

git submodule update --init --remote --recursive

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$1/v1/.well-known/ready; then
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
  rm -rf apps/weaviate/data-node-* || true
  docker compose -f apps/weaviate/docker-compose-replication.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker compose -f apps/multi-tenancy-concurrent-imports/docker-compose-importers.yml down --remove-orphans
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Run importer scripts in the background"
docker compose -f apps/multi-tenancy-concurrent-imports/docker-compose-importers.yml up schema-resetter
docker compose -f apps/multi-tenancy-concurrent-imports/docker-compose-importers.yml up -d importer-01 importer-02 importer-03 importer-04

echo "Run checker script in the foreground"
docker compose -f apps/multi-tenancy-concurrent-imports/docker-compose-importers.yml up corruption-checker --exit-code-from corruption-checker

echo "Success"
shutdown
