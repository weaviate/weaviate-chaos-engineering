#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/recall/ && docker build -t recall . )
( cd apps/recall-check/ && docker build -t recall-checker . )

rm -rf workdir
mkdir workdir
touch workdir/data.json

echo "Generate a dataset of 100k objects"
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" -t recall python3 generate.py

echo "Done generating."

export COMPOSE="apps/weaviate/docker-compose.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate

echo "Import into Weaviate..."
docker run --network host -v "$PWD/workdir/data.json:/workdir/data.json" -t recall python3 import.py

echo "Check Recall"
docker run --network host -v "$PWD/workdir/:/app/data" -t recall-checker

echo "Restart Weaviate"
docker compose -f $COMPOSE stop weaviate && \
  docker compose -f $COMPOSE start weaviate

wait_weaviate

echo "Check Recall again"
docker run --network host -v "$PWD/workdir/:/app/data" -t recall-checker

echo "Passed!"
shutdown
