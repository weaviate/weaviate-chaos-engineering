#!/bin/bash

# Replication latency bench. Set BASELINE_VERSION to run a same-runner A/B against
# $WEAVIATE_VERSION; otherwise single-version. Env: OBJECTS READS DIM CONSISTENCY
# ITERATIONS WARMUP BASELINE_VERSION COMPARE_TO.

set -e

source common.sh

export COMPOSE="apps/weaviate/docker-compose-replication.yml"

if [ -z "$WEAVIATE_VERSION" ]; then
  echo "ERROR: WEAVIATE_VERSION must be set (e.g. WEAVIATE_VERSION=local-optimized $0)"
  exit 1
fi

echo "Building replication-latency-bench image"
( cd apps/replication-latency-bench/ && docker build -t replication-latency-bench . )

# Bring up the cluster on $1, run the benchmark, write results to $2. If $3 is a
# readable results.json it is handed to the container as the COMPARE_TO baseline.
run_cluster_and_bench() {
  local version="$1" out_file="$2" compare_file="$3"

  # Start every phase from a clean slate: stale apps/weaviate/data* (e.g. from a
  # prior run on a reused machine) carries old RAFT cluster state that prevents
  # the fresh cluster from bootstrapping (it hangs trying to recover old peers).
  rm -rf apps/weaviate/data* 2>/dev/null || sudo rm -rf apps/weaviate/data* || true
  rm -rf workdir 2>/dev/null || sudo rm -rf workdir || true
  mkdir workdir

  local compare_env=()
  if [ -n "$compare_file" ] && [ -f "$compare_file" ]; then
    cp "$compare_file" workdir/baseline.json
    compare_env=(-e COMPARE_TO=/workdir/baseline.json)
  fi

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
    "${compare_env[@]}" \
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
  run_cluster_and_bench "$BASELINE_VERSION" "results-baseline.json" ""
  teardown_cluster
  run_cluster_and_bench "$candidate_version" "results.json" "results-baseline.json"
else
  run_cluster_and_bench "$WEAVIATE_VERSION" "results.json" "$COMPARE_TO"
fi

shutdown
echo "Success!"
