#!/bin/bash

set -eou pipefail

# The journey streams every node's log to stdout and terminates the containers itself,
# so the only copy of the logs left to scan is this tee. pipefail keeps go's exit code.
journey_log=/tmp/weaviate-downgrade-journey.log
(
  cd apps/downgrade-journey-raft-schema-force-from-snapshot/

  # remove any potential leftover data from previous runs
  sudo rm -rf data || true

  go run . 2>&1 | tee "$journey_log"
)
./check_panics.sh "$journey_log"
