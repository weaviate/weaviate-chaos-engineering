#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/rest-patch-stops-working-after-restart/ && docker build -t rest-patch-stops-working-after-restart . )

export COMPOSE="apps/weaviate/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate

echo "Run consecutive update operations"
docker run --network host -t rest-patch-stops-working-after-restart python3 rest-patch-stops-working-after-restart.py

echo "Restart Weaviate..."
docker compose -f $COMPOSE stop
docker compose -f $COMPOSE up -d

wait_weaviate

echo "Run consecutive update operations after restart"
docker run --network host -t rest-patch-stops-working-after-restart python3 rest-patch-stops-working-after-restart.py

echo "Passed!"
shutdown
