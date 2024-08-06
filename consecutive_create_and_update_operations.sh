#!/bin/bash

set -e 

source common.sh

echo "Building all required containers"
( cd apps/consecutive_create_and_update_operations/ && docker build -t consecutive_create_and_update_operations . )

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Run consecutive create and update operations"
docker run --network host -t consecutive_create_and_update_operations python3 consecutive_create_and_update_operations.py

echo "Passed!"
shutdown
