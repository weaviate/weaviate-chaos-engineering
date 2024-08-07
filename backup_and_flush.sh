#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/backup-and-flush/ && docker build -t backup_and_flush . )

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Perform a backup and verify that flushing is restablished after the backup is finished."
docker run --network host -v ./apps/weaviate/data:/data -t backup_and_flush python3 backup_and_flush.py

echo "Passed!"
shutdown
