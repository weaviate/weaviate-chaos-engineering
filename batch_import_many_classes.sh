#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/batch-import-many-classes/ && docker build -t batch_import_many_classes . )

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Run expensive import in the background"
docker run -d --network host -t batch_import_many_classes python3 expensive_batches.py

echo "Run class creation and deletion in the foreground - fail on timeout"
docker run --network host -t batch_import_many_classes python3 many_classes.py

echo "Passed!"
shutdown
