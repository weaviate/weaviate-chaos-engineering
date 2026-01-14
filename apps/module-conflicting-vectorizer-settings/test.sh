#!/bin/bash

set -eou pipefail

function echo_green() {
  green='\033[0;32m'
  nc='\033[0m'
  echo -e "${green}${*}${nc}"
}

function echo_yellow() {
  yellow='\033[0;33m'
  nc='\033[0m'
  echo -e "${yellow}${*}${nc}"
}

function echo_red() {
  red='\033[0;31m'
  nc='\033[0m'
  echo -e "${red}${*}${nc}"
}

function wait_weaviate() {
  local port="${1:-8080}" # Set default port to 8080 if $1 is not provided
  local timeout="${2:-120}" # Set default timeout to 120 seconds if $2 is not provided
  echo "Wait for Weaviate to be ready"
  for ((i=1; i<=timeout; i++)); do
    if curl -sf -o /dev/null localhost:$port/v1/.well-known/ready; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready on $port, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready in port ${port} after ${timeout}s"
  exit 1
}

function wait_weaviate_cluster() {
  wait_weaviate 8080
  wait_weaviate 8081
  wait_weaviate 8082 
}

function verify_weaviate_version() {
  local port="${1:-8080}" # Set default port to 8080 if $1 is not provided
  local is_version="${2}"
  version=$(curl localhost:$port/v1/meta | jq -r ".version")
  if [[ $version == $is_version ]]; then
    echo_green "Weaviate is running version: $version"
    return 0
  fi
  echo_red "Weaviate is running version: $version but should: $is_version"
  exit 1
}

function prepare() {
  go mod vendor
}

function run_tests() {
  local weaviate_version="${1}"
  local test_name="${2}"
  local verify_version="${3:-true}"
  export WEAVIATE_TEST_VERSION=$weaviate_version
  echo_yellow "Create collections on Weaviate v$weaviate_version"

  echo_yellow "Starting v$weaviate_version docker compose"
  docker compose up -d

  wait_weaviate_cluster

  if [[ "$verify_version" == "true" ]]; then
    verify_weaviate_version 8080 $weaviate_version
  fi

  echo_yellow "Run $test_name test"

  go test -v -count 1 -run $test_name .

  docker compose stop
  echo_yellow "Stopped v$weaviate_version docker compose"
}

if [[ "$WEAVIATE_VERSION" == "" ]]; then
  echo_red "Please specify Weaviate version to test by setting WEAVIATE_VERSION env variable"
  exit 1
fi

echo_green "Start Weaviate conflicting vectorizer settings tests"

prepare

run_tests 1.20.0 TestModuleSettings_v1_20
run_tests 1.24.0 TestModuleNamedVectorsSettings_v1_24
run_tests 1.27.0 TestModuleNamedVectorsSettings_v1_27
run_tests 1.32.6 TestModuleNamedVectorsSettings_v1_32
run_tests $WEAVIATE_VERSION TestUpdateCollection false

echo_green "Success"
