#!/bin/bash

set -eou pipefail

(
  cd apps/downgrade-journey-raft-schema/

  # remove any potential leftover data from previous runs
  rm -rf data

  go run .

  cd -
)
