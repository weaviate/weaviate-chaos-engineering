#!/bin/bash

set -e

function wait_weaviate_cluster() {
  echo "Wait for Weaviate to be ready"
  local node1_ready=false
  local node2_ready=false
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080; then
      echo "Weaviate node1 is ready"
      node1_ready=true
    fi

    if curl -sf -o /dev/null localhost:8081; then
      echo "Weaviate node2 is ready"
      node2_ready=true
    fi

    if $node1_ready && $node2_ready; then
      break
    fi

    echo "Weaviate cluster is not ready, trying again in 1s"
    sleep 1
  done
}

echo "Building all required containers"
( cd apps/backup_and_restore_crud/ && docker build -t backup_and_restore_crud \
  --build-arg backend="s3" --build-arg expected_shard_count=2 . )

export WEAVIATE_NODE_1_VERSION=$WEAVIATE_VERSION
export WEAVIATE_NODE_2_VERSION=$WEAVIATE_VERSION

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose-backup.yml up -d weaviate-node-1 weaviate-node-2 backup-s3

wait_weaviate_cluster

echo "Creating S3 bucket..."
docker-compose -f apps/weaviate/docker-compose-backup.yml up create-s3-bucket

echo "Run multi-node backup and restore CRUD operations"
docker run --network host -it backup_and_restore_crud python3 backup_and_restore_crud.py

echo "Passed!"
