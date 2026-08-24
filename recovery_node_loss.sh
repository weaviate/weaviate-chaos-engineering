#!/bin/bash

set -e

source common.sh

# Tests recovery from complete node loss: a node is killed and its disk wiped,
# then restarted with the same identity. After rejoining the cluster the empty
# node must be brought back in sync with the two untouched replicas
# (replication factor 3, async replication enabled).

export COMPOSE="apps/weaviate/docker-compose-replication.yml"
# Opt in to the self-recovery feature under test on all nodes. Self-recovery
# rides on the replica movement machinery, which has its own opt-in.
export SELF_RECOVERY_ENABLED=true
export REPLICA_MOVEMENT_ENABLED=true
# Kill async replication at the node level. The server forces the class-level
# asyncEnabled flag to true for replicated classes (observed on 1.38.0), and
# async replication backfills a wiped node object-by-object, which would let
# pre-feature versions pass this test. Verified on 1.38.0: with this set, a
# wiped node stays empty and direct ONE reads through it 404.
export ASYNC_REPLICATION_DISABLED=true

# Large enough that the rebuild takes a while, so the direct queries against
# node3 right after its restart land while recovery is still in progress.
SIZE=${SIZE:-300000}
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

# The class-level flag alone does not disable async replication (the server
# stores asyncEnabled: true regardless); ASYNC_REPLICATION_DISABLED above is
# what actually turns it off. Sent anyway to document intent.
echo "Creating schema with replication factor 3, async replication disabled"
docker run --network host --rm \
  -e CONFIG_REPLICATION_FACTOR=3 \
  -e CONFIG_ASYNC_REPLICATION=false \
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

# The sharp check, immediately after node3 is back up: read every object
# through node3's own port with consistency level ONE. Pre-fix versions serve
# the empty local shard and 404 (verified on 1.38.0). With self-recovery the
# reads must succeed the whole time: failing over to a healthy replica while
# the shard is still recovering, served locally once it has been promoted.
# ALL cannot discriminate here because a divergent replica is read-repaired
# rather than failing the read.
echo "Querying node3 directly right after restart (consistency level ONE)"
docker run --network host --rm \
  -e "CONFIG_OBJECT_COUNT=$SIZE" \
  -e CONFIG_HOST=http://localhost:8082 \
  -e CONFIG_CONSISTENCY_LEVEL=ONE \
  -t importer python3 run.py --action validate

echo "Waiting up to ${RECOVERY_TIMEOUT}s for node3 to re-sync from its peers"
wait_until_nodes_in_sync "$SIZE" "$RECOVERY_TIMEOUT"

echo "Validating that every object is readable with consistency level ALL"
docker run --network host --rm \
  -e "CONFIG_OBJECT_COUNT=$SIZE" \
  -e CONFIG_CONSISTENCY_LEVEL=ALL \
  -t importer python3 run.py --action validate

echo "Passed!"
