#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/backup_and_restore_crud/ && docker build -t backup_and_restore_crud . )

export COMPOSE="apps/weaviate/docker-compose.yml"
echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate

echo "Run backup and restore CRUD operations"
docker run --network host -t backup_and_restore_crud python3 backup_and_restore_crud.py

echo "Passed!"
shutdown
