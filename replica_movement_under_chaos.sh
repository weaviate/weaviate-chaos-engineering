#!/bin/bash

# Replica-movement-under-chaos e2e driver (k8s family). Assumes a pre-provisioned 3-node
# StatefulSet `weaviate` with REPLICA_MOVEMENT_ENABLED=true and a small
# PERSISTENCE_LSM_MAX_SEGMENT_SIZE (frequent compactions) — this script runs ONLY the test.
# Does NOT source common.sh (its compose-oriented traps misfire on k8s).

set -e

NS="${K8S_NAMESPACE:-weaviate}"

# Two forwarded ports per pod (HTTP + gRPC), each mapped to a distinct local pair so the app
# can pin one dual-protocol async client per node.
PIDS=()
kubectl port-forward -n "$NS" pod/weaviate-0 8080:8080 50051:50051 &
PIDS+=($!)
kubectl port-forward -n "$NS" pod/weaviate-1 8081:8080 50052:50051 &
PIDS+=($!)
kubectl port-forward -n "$NS" pod/weaviate-2 8082:8080 50053:50051 &
PIDS+=($!)
trap 'kill "${PIDS[@]}" 2>/dev/null' EXIT

wait_http_ready() {
  local port="$1"
  for _ in {1..120}; do
    if curl -sf -o /dev/null "localhost:${port}/v1/.well-known/ready"; then
      echo "Weaviate on :${port} is ready"
      return 0
    fi
    echo "Weaviate on :${port} not ready, retrying in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate on :${port} not ready after 120s"
  return 1
}

echo "Waiting for per-pod HTTP readiness"
wait_http_ready 8080
wait_http_ready 8081
wait_http_ready 8082

# No curl probe for gRPC; a short settle plus the app's bounded connect() retry is the real guard.
echo "Short gRPC settle"
sleep 5

echo "Building the test container"
( cd apps/replica-movement-chaos && docker build -t replica_movement_chaos . )

WEAVIATE_NODES="weaviate-0=host.docker.internal:8080:50051,weaviate-1=host.docker.internal:8081:50052,weaviate-2=host.docker.internal:8082:50053"

# Forward any tunables that are set in the environment through to the container.
docker_env_args=()
for var in DURATION COLLECTION TENANT_COUNT OBJECTS_PER_TENANT REPLICATION_FACTOR \
  BACKUP_ENABLED BACKUP_BACKEND BACKUP_DELAY_MIN BACKUP_DELAY_MAX \
  BACKUP_RESTORE_ENABLED BACKUP_RESTORE_MIN_FRACTION RESTORE_TIMEOUT RESTORE_VERIFY_TIMEOUT \
  MUTATE_CONCURRENCY MUTATE_INTERVAL_MS TENANT_INTERVAL_MS TENANT_CONFLICT_INJECT_RATE \
  MOVE_INTERVAL_MS MOVE_MAX_INFLIGHT MOVE_CONFLICT_INJECT_RATE MOVE_POLL_INTERVAL \
  PREFLIGHT_MOVE_TIMEOUT RETRY_BUDGET RETRY_BACKOFF_MS SETTLE_TIMEOUT DRAIN_TIMEOUT \
  NODE_LOCAL_SAMPLE NODE_LOCAL_CONCURRENCY NODE_LOCAL_CONVERGE_TIMEOUT \
  VERIFY_TIMEOUT VERIFY_CONCURRENCY SEED; do
  if [ -n "${!var:-}" ]; then
    docker_env_args+=(-e "${var}=${!var}")
  fi
done

echo "Starting the chaos test"
# --network host lets the in-container client reach the host-side port-forwards (Linux/CI default).
# macOS caveat: on Docker Desktop --network host does NOT reach host forwards; run
# `python3 run.py` directly on the host (with the same WEAVIATE_NODES) or use host.docker.internal.
container_id=$(docker run -d --network host \
  -e WEAVIATE_NODES="$WEAVIATE_NODES" \
  "${docker_env_args[@]}" \
  -t replica_movement_chaos python3 run.py)

echo "Following logs until the test completes"
docker logs -f "$container_id"

exit_code=$(docker inspect "$container_id" --format='{{.State.ExitCode}}')
echo "Container exited with code $exit_code"

# Remove the exited container so repeated local runs don't accumulate them. --rm is not an option
# because the exit code above must be read after the container has already stopped.
docker rm "$container_id" >/dev/null 2>&1 || true

exit "$exit_code"
