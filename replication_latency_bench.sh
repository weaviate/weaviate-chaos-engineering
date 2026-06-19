#!/bin/bash

# Replication performance A/B. Drives sustained concurrent load against a 3-node
# RF=3 cluster and compares two images on the replication path.
#
# Set BASELINE_VERSION to A/B against $WEAVIATE_VERSION; otherwise single-image.
# Each image's cluster is brought up ONCE and all ROUNDS run against it (long-lived,
# warm cluster) — far fewer bring-ups than per-round teardown, and lower round-to-
# round variance. Each round appends to results-baseline.json / results.json;
# results_to_summary.py aggregates them.
#
# Env: ROUNDS CONCURRENCY WARMUP_SECONDS DURATION_SECONDS READ_POOL DIM CONSISTENCY
#      BASELINE_VERSION.

set -e

source common.sh

export COMPOSE="apps/weaviate/docker-compose-replication.yml"
ROUNDS="${ROUNDS:-6}"

if [ -z "$WEAVIATE_VERSION" ]; then
  echo "ERROR: WEAVIATE_VERSION must be set (e.g. WEAVIATE_VERSION=1.38.0 $0)"
  exit 1
fi

echo "Building replication throughput bench image"
( cd apps/replication-latency-bench/ && docker build -t replication-latency-bench . )

mkdir -p workdir

# Bring the cluster up ONCE on $1, run the bench (which does all ROUNDS internally,
# recreating + preloading once per CL) into /workdir/$2, then tear it down.
run_phase() {
  local version="$1" out_file="$2"

  rm -rf apps/weaviate/data* 2>/dev/null || sudo rm -rf apps/weaviate/data* || true

  export WEAVIATE_VERSION="$version"
  echo "=== bring up weaviate:$version (long-lived, $ROUNDS rounds) ==="
  docker compose -f "$COMPOSE" up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
  wait_weaviate 8080 180 weaviate-node-1
  wait_weaviate 8081 180 weaviate-node-2
  wait_weaviate 8082 180 weaviate-node-3

  local i
  for i in $(seq 1 30); do
    curl -sf -o /dev/null localhost:2112/metrics && break
    [ "$i" -eq 30 ] && { echo "ERROR: metrics endpoint never came up"; exit 1; }
    sleep 1
  done

  docker run --rm --network host \
    -v "$PWD/workdir/:/workdir" \
    -e WEAVIATE_VERSION="$version" \
    -e RESULTS_PATH="/workdir/$out_file" \
    -e ROUNDS="${ROUNDS}" \
    -e CONCURRENCY="${CONCURRENCY}" \
    -e WARMUP_SECONDS="${WARMUP_SECONDS}" \
    -e DURATION_SECONDS="${DURATION_SECONDS}" \
    -e READ_POOL="${READ_POOL}" \
    -e DIM="${DIM}" \
    -e CONSISTENCY="${CONSISTENCY}" \
    --name replication-latency-bench -t replication-latency-bench

  docker compose -f "$COMPOSE" down --remove-orphans || true
  rm -rf apps/weaviate/data* 2>/dev/null || sudo rm -rf apps/weaviate/data* || true
}

# Fresh accumulation files.
rm -f workdir/results.json workdir/results-baseline.json

candidate_version="$WEAVIATE_VERSION"
if [ -n "$BASELINE_VERSION" ]; then
  echo "=== A/B over $ROUNDS rounds: baseline=$BASELINE_VERSION candidate=$candidate_version ==="
  run_phase "$BASELINE_VERSION" "results-baseline.json"
  run_phase "$candidate_version" "results.json"
  cp workdir/results-baseline.json results-baseline.json
else
  echo "=== single-image over $ROUNDS rounds: $candidate_version ==="
  run_phase "$candidate_version" "results.json"
fi

cp workdir/results.json results.json
shutdown
echo "Success!"
