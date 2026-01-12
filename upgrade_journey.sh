#!/bin/bash

set -eou pipefail

(
  cd apps/upgrade-journey/

  # remove any potential leftover data from previous runs
  sudo rm -rf data || true

  go run .
)
