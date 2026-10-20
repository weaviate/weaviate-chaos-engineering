#!/bin/bash

set -e

source common.sh

# Requires Weaviate >= 1.39 (weaviate/weaviate#11348): when a node shuts down
# gracefully, in-flight backups/restores coordinated by that node must be
# cancelled (participants aborted) and the global descriptor persisted to the
# backend with status CANCELED, so any surviving node reports that status.

export WEAVIATE_NODE_1_VERSION=$WEAVIATE_VERSION
export WEAVIATE_NODE_2_VERSION=$WEAVIATE_VERSION
export WEAVIATE_NODE_3_VERSION=$WEAVIATE_VERSION
export COMPOSE="apps/weaviate/docker-compose-backup-3nodes.yml"

SIZE=${SIZE:-300000}
BACKUP_CANCEL_ID="backup-cancel-on-node-down"
RESTORE_SOURCE_ID="restore-cancel-source"
# On SIGTERM the coordinator drains for up to ~45s before persisting the final
# descriptor; the stop timeout must exceed that so docker doesn't SIGKILL first.
STOP_TIMEOUT=90

function get_status() {
  local kind=$1 port=$2 id=$3
  local url="localhost:${port}/v1/backups/s3/${id}"
  if [ "$kind" = "restore" ]; then
    url="${url}/restore"
  fi
  curl -sS "$url" 2>/dev/null | jq -r '.status // empty' 2>/dev/null || true
}

function wait_for_status() {
  local kind=$1 port=$2 id=$3 want=$4 retries=$5
  local status
  for ((i=1; i<=retries; i++)); do
    status=$(get_status "$kind" "$port" "$id")
    echo "$kind $id on port $port: status=${status:-<none>} (want $want, attempt $i/$retries)"
    if [ "$status" = "$want" ]; then
      return 0
    fi
    case "$status" in
      SUCCESS|FAILED|CANCELED)
        echo "ERROR: $kind $id reached terminal status $status while waiting for $want"
        return 1
        ;;
    esac
    sleep 2
  done
  echo "ERROR: $kind $id did not reach status $want after $retries attempts"
  return 1
}

function start_operation() {
  local kind=$1 id=$2
  local url="localhost:8080/v1/backups/s3"
  local data="{\"id\": \"${id}\", \"include\": [\"DemoClass\"]}"
  if [ "$kind" = "restore" ]; then
    url="${url}/${id}/restore"
    data='{"include": ["DemoClass"]}'
  fi

  local body status
  body=$(curl -s -XPOST "$url" -H 'content-type: application/json' -d "$data")
  status=$(echo "$body" | jq -r '.status // empty')
  if [ "$status" != "STARTED" ]; then
    echo "ERROR: $kind $id did not start, response: $body"
    return 1
  fi
  echo "$kind $id started"
}

function wait_for_cluster_convergence() {
  # Readiness on node1 only means the node itself is up; gossip may not have
  # re-learned the other nodes' addresses yet, and a backup needs all of them.
  local retries=60 healthy
  for ((i=1; i<=retries; i++)); do
    healthy=$(curl -s localhost:8080/v1/nodes | jq -r '[.nodes[]? | select(.status == "HEALTHY")] | length')
    echo "cluster convergence: ${healthy:-0}/3 nodes HEALTHY (attempt $i/$retries)"
    if [ "$healthy" = "3" ]; then
      return 0
    fi
    sleep 2
  done
  echo "ERROR: cluster did not converge to 3 HEALTHY nodes after $retries attempts"
  return 1
}

function restart_node_1() {
  echo "Restarting node1"
  docker compose -f $COMPOSE up -d weaviate-node-1
  wait_weaviate 8080 120 weaviate-node-1
  wait_for_cluster_convergence
}

echo "Building all required containers"
( cd apps/importer/ && docker build -t importer . )

echo "Starting Weaviate cluster..."
docker compose -f $COMPOSE up -d weaviate-node-1 weaviate-node-2 weaviate-node-3 backup-s3

wait_weaviate 8080 120 weaviate-node-1
wait_weaviate 8081 120 weaviate-node-2
wait_weaviate 8082 120 weaviate-node-3

echo "Creating S3 bucket..."
docker compose -f $COMPOSE up create-s3-bucket

echo "Importing $SIZE objects across 3 shards"
docker run \
  -e 'DIMENSIONS=48' \
  -e 'SHARDS=3' \
  -e "SIZE=$SIZE" \
  -e 'BATCH_SIZE=128' \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  -t importer

echo ""
echo "=== Test 1: backup is CANCELED when the coordinating node goes down ==="
start_operation backup "$BACKUP_CANCEL_ID"

echo "Stopping node1 (coordinator) gracefully while the backup is in progress"
docker compose -f $COMPOSE stop -t $STOP_TIMEOUT weaviate-node-1

echo "Waiting for backup $BACKUP_CANCEL_ID to be reported as CANCELED by node2"
wait_for_status backup 8081 "$BACKUP_CANCEL_ID" CANCELED 60

restart_node_1

echo ""
echo "=== Preparing restore test: create a full backup, then drop the class ==="
start_operation backup "$RESTORE_SOURCE_ID"
wait_for_status backup 8080 "$RESTORE_SOURCE_ID" SUCCESS 300

echo "Deleting DemoClass so it can be restored"
curl -sf -XDELETE localhost:8080/v1/schema/DemoClass > /dev/null

deleted=false
for _ in {1..30}; do
  schema=$(curl -sf localhost:8080/v1/schema 2>/dev/null || true)
  if [ -z "$schema" ]; then
    echo "Failed to fetch schema, waiting..."
    sleep 2
    continue
  fi

  if ! echo "$schema" | jq -e '.classes[]? | select(.class == "DemoClass")' > /dev/null; then
    echo "DemoClass deleted"
    deleted=true
    break
  fi
  echo "DemoClass still present, waiting..."
  sleep 2
done

if [ "$deleted" != "true" ]; then
  echo "ERROR: DemoClass was not deleted after waiting"
  exit 1
fi

echo ""
echo "=== Test 2: restore is CANCELED when the coordinating node goes down ==="
start_operation restore "$RESTORE_SOURCE_ID"

echo "Stopping node1 (coordinator) gracefully while the restore is in progress"
docker compose -f $COMPOSE stop -t $STOP_TIMEOUT weaviate-node-1

echo "Waiting for restore $RESTORE_SOURCE_ID to be reported as CANCELED by node2"
wait_for_status restore 8081 "$RESTORE_SOURCE_ID" CANCELED 60

restart_node_1

echo "Passed!"