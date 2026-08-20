#!/bin/bash

set -e

source common.sh

# Tests recovery from complete node loss: a node is killed and its disk wiped,
# then restarted with the same identity. After rejoining the cluster the empty
# node must be brought back in sync with the two untouched replicas
# (replication factor 3, async replication enabled).

export COMPOSE="apps/weaviate/docker-compose-replication.yml"
# Opt in to the self-recovery feature under test on all nodes.
export SELF_RECOVERY_ENABLED=true

SIZE=${SIZE:-100000}
# How long the wiped node has to catch up with its peers after restarting.
RECOVERY_TIMEOUT=${RECOVERY_TIMEOUT:-600}

function node_object_counts() {
  # Prints "<node-name> <local object count of Document>" per line, summing
  # the node's shards. The nodes endpoint reports each node's local stats, so
  # with replication factor 3 every node must eventually report the full count.
  curl -s "localhost:8080/v1/nodes?output=verbose" |
    jq -r '.nodes[] | "\(.name) \([.shards[]? | select(.class == "Document") | .objectCount] | add // 0)"'
}

function wait_until_nodes_in_sync() {
  local want=$1 timeout=$2
  local start=$(date +%s) counts
  while true; do
    counts=$(node_object_counts)
    echo "Per-node object counts (want $want on all 3 nodes):"
    echo "$counts" | sed 's/^/  /'
    if [ "$(echo "$counts" | wc -l)" -eq 3 ] && \
       [ -z "$(echo "$counts" | awk -v want="$want" '$2 != want')" ]; then
      echo "All nodes report $want objects"
      return 0
    fi
    if (( $(date +%s) - start > timeout )); then
      echo "ERROR: nodes did not converge to $want objects within ${timeout}s"
      return 1
    fi
    sleep 5
  done
}

echo "Building all required containers"
( cd apps/replicated-import/ && docker build -t importer . )

echo "Starting Weaviate cluster..."
docker compose -f $COMPOSE up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080 120 weaviate-node-1
wait_weaviate 8081 120 weaviate-node-2
wait_weaviate 8082 120 weaviate-node-3

echo "Creating schema with replication factor 3 and async replication enabled"
docker run --network host --rm \
  -e CONFIG_REPLICATION_FACTOR=3 \
  -e CONFIG_ASYNC_REPLICATION=true \
  -t importer python3 run.py --action schema

echo "Importing $SIZE objects (writes and validation with consistency level ALL)"
docker run --network host --rm \
  -e "CONFIG_OBJECT_COUNT=$SIZE" \
  -e CONFIG_CONSISTENCY_LEVEL=ALL \
  -t importer python3 run.py --action import

echo "Waiting for all 3 nodes to hold $SIZE objects locally"
wait_until_nodes_in_sync "$SIZE" 300

echo ""
echo "=== Killing node3 and wiping its disk ==="
docker compose -f $COMPOSE kill weaviate-node-3
rm -rf apps/weaviate/data-node-3 2>/dev/null || sudo rm -rf apps/weaviate/data-node-3

echo "Restarting node3 with an empty disk"
docker compose -f $COMPOSE up -d weaviate-node-3
wait_weaviate 8082 300 weaviate-node-3

echo "Waiting up to ${RECOVERY_TIMEOUT}s for node3 to re-sync from its peers"
wait_until_nodes_in_sync "$SIZE" "$RECOVERY_TIMEOUT"

echo "Validating that every object is readable with consistency level ALL"
docker run --network host --rm \
  -e "CONFIG_OBJECT_COUNT=$SIZE" \
  -e CONFIG_CONSISTENCY_LEVEL=ALL \
  -t importer python3 run.py --action validate

echo "Passed!"
