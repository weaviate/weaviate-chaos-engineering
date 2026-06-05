#!/bin/bash

# Replication latency bench. Set BASELINE_VERSION to run a same-runner A/B against
# $WEAVIATE_VERSION; otherwise single-version. Env: OBJECTS READS DIM CONSISTENCY
# ITERATIONS WARMUP BASELINE_VERSION.

set -e

source common.sh

export COMPOSE="apps/weaviate/docker-compose-replication.yml"

if [ -z "$WEAVIATE_VERSION" ]; then
  echo "ERROR: WEAVIATE_VERSION must be set (e.g. WEAVIATE_VERSION=local-optimized $0)"
  exit 1
fi

echo "Building replication-latency-bench image"
( cd apps/replication-latency-bench/ && docker build -t replication-latency-bench . )

# Bring up the cluster on $1, run the benchmark, write results to $2.
run_cluster_and_bench() {
  local version="$1" out_file="$2"

  # Clean slate: stale apps/weaviate/data* carries old RAFT state that stops the
  # fresh cluster from bootstrapping.
  rm -rf apps/weaviate/data* 2>/dev/null || sudo rm -rf apps/weaviate/data* || true
  rm -rf workdir 2>/dev/null || sudo rm -rf workdir || true
  mkdir workdir

  export WEAVIATE_VERSION="$version"
  echo "Starting 3-node replicated cluster (weaviate:$version)..."
  docker compose -f "$COMPOSE" up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
  wait_weaviate 8080 180 weaviate-node-1
  wait_weaviate 8081 180 weaviate-node-2
  wait_weaviate 8082 180 weaviate-node-3

  # Confirm monitoring is actually exposed before running the workload.
  echo "Checking metrics endpoint on node-1..."
  local i
  for i in $(seq 1 30); do
    if curl -sf -o /dev/null localhost:2112/metrics; then
      echo "Metrics endpoint is up"
      break
    fi
    if [ "$i" -eq 30 ]; then
      echo "ERROR: metrics endpoint localhost:2112/metrics never came up"
      exit 1
    fi
    sleep 1
  done

  echo "Running benchmark against weaviate:$version..."
  # Pass tunables through as-is (empty -> bench.py applies its own defaults, so
  # OBJECTS/READS/ITERATIONS/etc. live in ONE place: bench.py).
  docker run --rm --network host \
    -v "$PWD/workdir/:/workdir" \
    -e WEAVIATE_VERSION="$version" \
    -e OBJECTS="${OBJECTS}" \
    -e READS="${READS}" \
    -e DIM="${DIM}" \
    -e CONSISTENCY="${CONSISTENCY}" \
    -e ITERATIONS="${ITERATIONS}" \
    -e WARMUP="${WARMUP}" \
    --name replication-latency-bench -t replication-latency-bench

  cp workdir/results.json "$out_file"
  echo "Results written to $out_file"
}

# Tear down the cluster and wipe its data so the next version starts clean. Does
# NOT touch results-*.json at the repo root.
teardown_cluster() {
  docker compose -f "$COMPOSE" down --remove-orphans || true
  rm -rf apps/weaviate/data* 2>/dev/null || sudo rm -rf apps/weaviate/data* || true
}

if [ -n "$BASELINE_VERSION" ]; then
  echo "=== same-runner A/B: baseline=$BASELINE_VERSION  candidate=$WEAVIATE_VERSION ==="
  # Capture the candidate up front: run_cluster_and_bench exports WEAVIATE_VERSION
  # for compose, so the baseline phase would otherwise clobber the global and the
  # candidate phase would re-run the baseline image.
  candidate_version="$WEAVIATE_VERSION"
  run_cluster_and_bench "$BASELINE_VERSION" "results-baseline.json"
  teardown_cluster
  run_cluster_and_bench "$candidate_version" "results.json"
else
  run_cluster_and_bench "$WEAVIATE_VERSION" "results.json"
fi

shutdown
echo "Success!"
