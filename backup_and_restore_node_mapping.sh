#!/bin/bash

source common.sh

set -e

echo "Building all required containers"
( cd apps/backup_and_restore_node_mapping/ && docker build -t backup_and_restore_node_mapping \
  --build-arg backend="s3" --build-arg expected_shard_count=3 . )

export WEAVIATE_NODE_1_VERSION=$WEAVIATE_VERSION
export WEAVIATE_NODE_2_VERSION=$WEAVIATE_VERSION
export WEAVIATE_NODE_3_VERSION=$WEAVIATE_VERSION

# Generate backup name
BACKUP_NAME="$(date +%s)_node_mapping_test"

# Phase 1: Start cluster with original node names (node1, node2, node3)
export COMPOSE="apps/weaviate/docker-compose-backup-3nodes.yml"

echo "=== PHASE 1: Starting cluster with original node names (node1, node2, node3) ==="
echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d weaviate-node-1 weaviate-node-2 weaviate-node-3 backup-s3

wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Creating S3 bucket..."
docker compose -f $COMPOSE up create-s3-bucket

echo "Run backup phase"
docker run --network host -e BACKUP_NAME="$BACKUP_NAME" -t backup_and_restore_node_mapping python3 backup_and_restore_node_mapping.py backup

echo "Stopping cluster with original node names..."
docker compose -f $COMPOSE down --remove-orphans



# Clean data directories to remove old cluster state before starting with new node names
# This is critical: the old cluster state (node1, node2, node3) conflicts with new names (new_node1, new_node2, new_node3)
# The backup is stored in S3, so we can safely clean local data - restore will pull from S3
echo "Cleaning data directories to remove old cluster state..."
mkdir -p apps/weaviate/data-node-1 apps/weaviate/data-node-2 apps/weaviate/data-node-3
rm -rf apps/weaviate/data-node-1/* apps/weaviate/data-node-2/* apps/weaviate/data-node-3/* 2>/dev/null || true

# Verify data directories are empty
echo "Verifying data directories are empty..."
for node_dir in apps/weaviate/data-node-1 apps/weaviate/data-node-2 apps/weaviate/data-node-3; do
    file_count=$(find "$node_dir" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)
    if [ "$file_count" -gt 0 ]; then
        echo "ERROR: $node_dir is not empty! Found $file_count items:"
        ls -la "$node_dir" | head -10
        exit 1
    fi
done
echo "Confirmed: All data directories are empty. Starting fresh cluster with renamed nodes..."


# Wait a moment to ensure containers are fully stopped
sleep 5
# Phase 2: Start cluster with renamed nodes (new_node1, new_node2, new_node3)
export COMPOSE="apps/weaviate/docker-compose-backup-3nodes-renamed.yml"

echo "=== PHASE 2: Starting cluster with renamed nodes (new_node1, new_node2, new_node3) ==="
echo "Starting Weaviate with renamed nodes..."
docker compose -f $COMPOSE up -d weaviate-node-1 weaviate-node-2 weaviate-node-3 backup-s3

echo "Waiting for nodes to be ready (allowing time for cluster formation)..."
wait_weaviate 8080 180
wait_weaviate 8081 180
wait_weaviate 8082 180

# Give cluster additional time to fully form after all nodes are ready
echo "Waiting for cluster to fully form..."
sleep 10

echo "Run restore phase with node mapping"
docker run --network host -e BACKUP_NAME="$BACKUP_NAME" -t backup_and_restore_node_mapping python3 backup_and_restore_node_mapping.py restore

echo "Stopping cluster with renamed nodes..."
docker compose -f $COMPOSE down

echo "=== PHASE 3: Restart cluster and verify data persists ==="
echo "Starting cluster again to verify data persisted..."
docker compose -f $COMPOSE up -d weaviate-node-1 weaviate-node-2 weaviate-node-3 backup-s3

echo "Waiting for nodes to be ready after restart..."
wait_weaviate 8080 180
wait_weaviate 8081 180
wait_weaviate 8082 180

# Give cluster additional time to fully form after all nodes are ready
echo "Waiting for cluster to fully form..."
sleep 10

echo "Verify data still exists after restart (without restore)"
docker run --network host -e BACKUP_NAME="$BACKUP_NAME" -t backup_and_restore_node_mapping python3 backup_and_restore_node_mapping.py verify

echo "Stopping cluster..."
docker compose -f $COMPOSE down

echo "Passed!"
shutdown
