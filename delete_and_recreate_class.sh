#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/delete_and_recreate && docker build -t delete_and_recreate . )

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Run consecutive create and update operations"
docker run --network host -t delete_and_recreate python3 delete_and_recreate.py

echo "Passed!"
shutdown
