#!/bin/bash

set -eou pipefail

(
  cd apps/downgrade-journey-raft-schema-force-from-snapshot/

  # remove any potential leftover data from previous runs
  sudo rm -rf data || true

  go run .
)
