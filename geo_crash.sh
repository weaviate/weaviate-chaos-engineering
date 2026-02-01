#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/geo-crash/ && docker build -t geo_crash . )

export COMPOSE="apps/weaviate-no-restart-on-crash/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate 8080 120 weaviate

echo "Import geo props with many duplicates (lots of hnsw commit logging)"
docker run --network host -t geo_crash python3 run.py

echo "Passed!"
shutdown
