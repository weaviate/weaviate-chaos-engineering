#!/bin/bash

set -eou pipefail

(
  cd apps/upgrade-journey/

  # remove any potential leftover data from previous runs
  rm -rf data

  go run .
)
