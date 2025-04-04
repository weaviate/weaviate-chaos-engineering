#!/usr/bin/env bash

set -eou pipefail

if [[ -z "${WEAVIATE_VERSION}" ]]; then
  echo "WEAVIATE_VERSION is undefined"
  exit 1
fi

if [[ -z "${PERSISTENCE_LSM_ACCESS_STRATEGY}" ]]; then
  echo "PERSISTENCE_LSM_ACCESS_STRATEGY is undefined"
  exit 1
fi

pushd ./apps/upgrade-journey-object-nested-properties

./run_tests.sh
