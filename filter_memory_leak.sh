#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/filter-memory-leak// && docker build -t leaker . )

export COMPOSE="apps/weaviate-no-restart-on-crash/docker-compose-with-memlimit.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate

echo "Run backup and restore CRUD operations"
docker run --network host -t leaker python3 run.py

echo "Passed!"
shutdown
