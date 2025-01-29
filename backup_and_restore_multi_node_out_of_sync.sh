#!/bin/bash
# This pipeline is based on the following scenario: https://semi-technology.atlassian.net/browse/WEAVIATE-737
# which caused a disruptive issue in one of our customers' production environment.

set -e

function wait_weaviate_cluster() {
  echo "Wait for Weaviate to be ready"
  local node1_ready=false
  local node2_ready=false
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080/v1/.well-known/ready; then
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
( cd apps/backup_and_restore_out_of_sync/ && docker build -t backup_and_restore_out_of_sync \
  --build-arg backend="s3" . )

export WEAVIATE_NODE_1_VERSION=$WEAVIATE_VERSION
export WEAVIATE_NODE_2_VERSION=$WEAVIATE_VERSION

export COMPOSE="apps/weaviate/docker-compose-backup.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d weaviate-node-1 weaviate-node-2 backup-s3

wait_weaviate_cluster

echo "Creating S3 bucket..."
docker compose -f $COMPOSE up create-s3-bucket

echo "Run multi-node backup and restore which affects schema"
docker run --network host -t backup_and_restore_out_of_sync python3 backup_and_restore_out_of_sync.py

echo "Passed!"
