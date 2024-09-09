#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/multi-node-references && docker build -t ref-importer . )

export COMPOSE="apps/weaviate/docker-compose-replication.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
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
  exit 1
fi

echo "Check for error logs"
errors="$(docker compose -f $COMPOSE logs 2>&1 | grep memberlist | grep error | wc -l | tr -d '[:space:]')"
if (( $warnings > 0 )); then
  docker compose -f $COMPOSE logs 2>&1 | grep memberlist | grep error
  echo "too many errors ($errors)"
  exit 1
fi

echo "Passed!"
shutdown
