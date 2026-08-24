#!/usr/bin/env bash
# Namespace graduation e2e: backup one namespace off a shared namespaced cluster, restore it onto a
# dedicated 3-node cluster, scale rf=1->3, delete the source namespace, then assert the migration.
#
# This script owns the rig: both clusters are its own compose projects, provisioned here.
#
# common.sh is deliberately NOT sourced: its shutdown() removes every container on the machine and
# deletes apps/weaviate/data*, and it is wired to an unconditional EXIT trap. The readiness poll and
# the teardown below are the two pieces of it this script needs, scoped to its own two projects.
#
# Usage (from the repo root):
#   WEAVIATE_VERSION=1.38.0 ./namespace_graduation.sh            # full journey
#   WEAVIATE_VERSION=1.38.0 ./namespace_graduation.sh preflight   # rig check only
#   KEEP_CLUSTERS=1 WEAVIATE_VERSION=1.38.0 ./namespace_graduation.sh   # leave both clusters up

set -euo pipefail

MODE="${1:-journey}"

# Both compose files interpolate this into their image tag; the app receives the same value and
# records it in its config summary.
: "${WEAVIATE_VERSION:?set WEAVIATE_VERSION, e.g. WEAVIATE_VERSION=1.38.0 ./namespace_graduation.sh}"

# The one home for the static root keys: the compose files interpolate them into
# AUTHENTICATION_APIKEY_ALLOWED_KEYS and the app container receives the same values.
export SOURCE_ROOT_API_KEY="${SOURCE_ROOT_API_KEY:-nsgrad-source-root-key}"
export TARGET_ROOT_API_KEY="${TARGET_ROOT_API_KEY:-nsgrad-target-root-key}"

SRC_FILE="apps/weaviate/docker-compose-namespaces-source.yml"
SRC_PROJECT="nsgrad-src"
TGT_FILE="apps/weaviate/docker-compose-namespaces-target.yml"
TGT_PROJECT="nsgrad-tgt"
NETWORK="nsgrad-shared"

KEEP_CLUSTERS="${KEEP_CLUSTERS:-0}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-180}"

# Host ports the two compose files publish. config.py defaults to the same ones.
SRC_HTTP_PORTS="18080"
TGT_HTTP_PORTS="18180 18181 18182"

dump_service_logs() {
  local project=$1
  local file=$2
  local service=$3
  echo "===== ${project}/${service}: first 30 lines ====="
  docker compose -p "$project" -f "$file" logs --no-color --no-log-prefix "$service" 2>&1 | head -30 || true
  echo "===== ${project}/${service}: last 100 lines ====="
  docker compose -p "$project" -f "$file" logs --no-color --no-log-prefix --tail 100 "$service" 2>&1 || true
}

# Runs before teardown on a failure: `down -v` destroys the containers, so the evidence has to
# reach the run log first. The app container's own output is already streamed by `docker logs -ft`.
dump_cluster_logs() {
  echo "Dumping cluster logs before teardown"
  dump_service_logs "$SRC_PROJECT" "$SRC_FILE" weaviate
  local service
  for service in weaviate-node-1 weaviate-node-2 weaviate-node-3; do
    dump_service_logs "$TGT_PROJECT" "$TGT_FILE" "$service"
  done
}

# `down -v` removes the named volumes with the containers, so consecutive runs start from empty
# clusters and no stale RAFT state survives. The network is removed last: both projects reference
# it as external, so it can only go once nothing is attached.
compose_down() {
  docker compose -p "$TGT_PROJECT" -f "$TGT_FILE" down -v --remove-orphans || true
  docker compose -p "$SRC_PROJECT" -f "$SRC_FILE" down -v --remove-orphans || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}

teardown() {
  local code=$?
  trap - EXIT
  if [ "$code" -ne 0 ]; then
    dump_cluster_logs
  fi
  if [ "$KEEP_CLUSTERS" = "1" ]; then
    echo "KEEP_CLUSTERS=1 — leaving projects ${SRC_PROJECT} and ${TGT_PROJECT} up"
    exit "$code"
  fi
  echo "Tearing down both clusters"
  compose_down
  exit "$code"
}

wait_ready() {
  local port=$1
  local waited=0
  until curl -sf "http://localhost:${port}/v1/.well-known/ready" >/dev/null; do
    if [ "$waited" -ge "$READY_TIMEOUT_S" ]; then
      echo "port ${port} never became ready within ${READY_TIMEOUT_S}s"
      exit 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
  echo "port ${port} is ready"
}

# The bucket one-shot is a readiness gate, not a fire-and-forget: until it has exited 0 the bucket
# may not exist, and the app's first backend call then answers 500 "The specified bucket does not
# exist". Both modes pass through here, so preflight gets the same guarantee as the journey.
wait_bucket() {
  local waited=0
  local container_id
  container_id=$(docker compose -p "$SRC_PROJECT" -f "$SRC_FILE" ps -aq create-s3-bucket)
  if [ -z "$container_id" ]; then
    echo "the create-s3-bucket container was never created — the backup bucket does not exist"
    exit 1
  fi
  while true; do
    local state
    local code
    state=$(docker inspect -f '{{.State.Status}}' "$container_id")
    code=$(docker inspect -f '{{.State.ExitCode}}' "$container_id")
    if [ "$state" = "exited" ]; then
      if [ "$code" = "0" ]; then
        echo "backup bucket nsgrad-backups is ready"
        return 0
      fi
      echo "create-s3-bucket exited ${code}: the backup bucket was not created"
      docker logs "$container_id" 2>&1 || true
      exit 1
    fi
    if [ "$waited" -ge "$READY_TIMEOUT_S" ]; then
      echo "create-s3-bucket did not finish within ${READY_TIMEOUT_S}s (state ${state})"
      docker logs "$container_id" 2>&1 || true
      exit 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

echo "Building the namespace-graduation container"
(cd apps/namespace-graduation && docker build -t namespace_graduation .)

# Pre-clean before the trap is installed: an aborted previous run can leave containers and volumes
# behind, and a reused volume carries RAFT state from a cluster that no longer exists.
echo "Removing anything left behind by a previous run"
compose_down

echo "Creating the ${NETWORK} network"
docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null

trap teardown EXIT

echo "Starting the namespaced source cluster and minio"
docker compose -p "$SRC_PROJECT" -f "$SRC_FILE" up -d weaviate minio

echo "Creating the backup bucket"
docker compose -p "$SRC_PROJECT" -f "$SRC_FILE" up create-s3-bucket

echo "Starting the 3-node target cluster"
docker compose -p "$TGT_PROJECT" -f "$TGT_FILE" up -d

# Every target node, not just the first: RAFT_BOOTSTRAP_EXPECT is 3, so the target has no leader
# until all three are up.
for port in $SRC_HTTP_PORTS $TGT_HTTP_PORTS; do
  wait_ready "$port"
done

wait_bucket

echo "Starting ${MODE}"
# The app reaches both clusters through their published host ports, via host.docker.internal.
# --network host is not used: on Docker Desktop for macOS it does not reach published ports.
container_id=$(docker run -d --add-host=host.docker.internal:host-gateway \
  -e WEAVIATE_VERSION \
  -e RUN_ID \
  -e WEAVIATE_HOST_ADDR \
  -e SOURCE_ROOT_API_KEY \
  -e TARGET_ROOT_API_KEY \
  -e SOURCE_HTTP_PORTS \
  -e SOURCE_GRPC_PORTS \
  -e TARGET_HTTP_PORTS \
  -e TARGET_GRPC_PORTS \
  -e NAMESPACE_PREFIX \
  -e NEIGHBOUR_NAMESPACE_COUNT \
  -e COLLECTION_PREFIX \
  -e COLLECTIONS_PER_NAMESPACE \
  -e OBJECTS_PER_COLLECTION \
  -e VECTOR_DIM \
  -e USERS_PER_NAMESPACE \
  -e NEIGHBOUR_SET_TARGET \
  -e BACKUP_BACKEND \
  -e BACKUP_ID_PREFIX \
  -e TARGET_REPLICATION_FACTOR \
  -e ALLOW_TARGET_STORE_REPLACEMENT \
  -e POLL_INTERVAL_S \
  -e REST_CONNECT_TIMEOUT_S \
  -e REST_READ_TIMEOUT_S \
  -e RAFT_VISIBILITY_TIMEOUT_S \
  -e BACKUP_TIMEOUT_S \
  -e RESTORE_TIMEOUT_S \
  -e SCALE_OP_TIMEOUT_S \
  -e SHARDING_STATE_TIMEOUT_S \
  -e NAMESPACE_DELETE_TIMEOUT_S \
  -e PER_REPLICA_SWEEP_TIMEOUT_S \
  -e PER_REPLICA_SWEEP_CONCURRENCY \
  -e COUNT_CONVERGE_FLOOR_S \
  -e LOAD_PAUSE_TIMEOUT_S \
  -e NEIGHBOUR_LOAD_OPS_PER_SECOND \
  -e RESTORE_NODE_MAPPING \
  -e RESTORE_INCLUDE \
  -t namespace_graduation python3 run.py "$MODE")

echo "Following the logs until ${MODE} completes"
docker logs -ft "$container_id"

# `docker wait` blocks until the container stops and prints its exit code, so a broken log stream
# cannot read a still-running container's ExitCode as 0. It returns immediately once it has stopped.
exit_code=$(docker wait "$container_id")
echo "Container exited with code $exit_code"
exit "$exit_code"
