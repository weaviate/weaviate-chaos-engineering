#!/bin/bash

# replication_latency_bench.sh
# -----------------------------
# Measures replication request latency on a replicated Weaviate cluster, and in
# particular the impact of the weaviate-core change
#
#     perf(replica): short-circuit local-node replica calls in-process
#
# Brings up a 3-node replicated cluster with Prometheus monitoring enabled,
# drives a write + read workload against the coordinator (node-1, which always
# hosts the RF=3 shard and is therefore always a local replica leg), and reads
# the request-level latency histograms weaviate already exports
# (grpc_server_request_duration_seconds / http_request_duration_seconds) — the
# metrics that sit above the replica fan-out and so reflect the local short-circuit.
#
# A single run reports absolute latency (CI uses this to surface the numbers).
# To prove an improvement, run it A/B over the image tag — the repo's standard
# WEAVIATE_VERSION mechanism:
#
#     # baseline (parent of the optimisation commit)
#     WEAVIATE_VERSION=local-baseline ./replication_latency_bench.sh
#     mv results.json results-baseline.json
#
#     # optimised build
#     WEAVIATE_VERSION=local-optimized COMPARE_TO=results-baseline.json \
#       ./replication_latency_bench.sh
#
# Build the two images in the weaviate core repo with `make weaviate-image`
# (tag them, e.g. `local-baseline` / `local-optimized`) before running.
#
# Tunables (env): OBJECTS, BATCH_SIZE, READS, DIM, CONSISTENCY (default "ONE,ALL"),
# ITERATIONS (timed runs per level, default 3), WARMUP (untimed runs, default 1),
# COMPARE_TO (path to a prior results.json to print a delta against).

set -e

source common.sh

export COMPOSE="apps/weaviate/docker-compose-replication-metrics.yml"

if [ -z "$WEAVIATE_VERSION" ]; then
  echo "ERROR: WEAVIATE_VERSION must be set (e.g. WEAVIATE_VERSION=local-optimized $0)"
  exit 1
fi

echo "Building replication-latency-bench image"
( cd apps/replication-latency-bench/ && docker build -t replication-latency-bench . )

rm -rf workdir 2>/dev/null || sudo rm -rf workdir || true
mkdir workdir

echo "Starting 3-node replicated cluster (weaviate:$WEAVIATE_VERSION)..."
docker compose -f "$COMPOSE" up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080 180 weaviate-node-1
wait_weaviate 8081 180 weaviate-node-2
wait_weaviate 8082 180 weaviate-node-3

# Confirm monitoring is actually exposed before running the workload.
echo "Checking metrics endpoint on node-1..."
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

# Pass a baseline results file through to the container for inline delta output.
COMPARE_MOUNT=()
COMPARE_ENV=()
if [ -n "$COMPARE_TO" ] && [ -f "$COMPARE_TO" ]; then
  cp "$COMPARE_TO" workdir/baseline.json
  COMPARE_ENV=(-e COMPARE_TO=/workdir/baseline.json)
fi

echo "Running benchmark..."
docker run --network host \
  -v "$PWD/workdir/:/workdir" \
  -e WEAVIATE_VERSION="$WEAVIATE_VERSION" \
  -e OBJECTS="${OBJECTS:-5000}" \
  -e BATCH_SIZE="${BATCH_SIZE:-10}" \
  -e READS="${READS:-5000}" \
  -e DIM="${DIM:-32}" \
  -e CONSISTENCY="${CONSISTENCY:-ONE,ALL}" \
  -e ITERATIONS="${ITERATIONS:-3}" \
  -e WARMUP="${WARMUP:-1}" \
  "${COMPARE_ENV[@]}" \
  --name replication-latency-bench -t replication-latency-bench

# Surface the machine-readable results next to the repo root for the A/B step.
cp workdir/results.json results.json
echo "Results written to results.json"

shutdown
echo "Success!"
