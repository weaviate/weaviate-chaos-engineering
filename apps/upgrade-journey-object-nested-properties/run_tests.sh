#!/usr/bin/env bash

set -eou pipefail

weaviate_version=$WEAVIATE_VERSION

function echo_green() {
  green='\033[0;32m'
  nc='\033[0m'
  echo -e "${green}${*}${nc}"
}

function wait_weaviate() {
  local port="${1:-8080}" # Set default port to 8080 if $1 is not provided
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$port/v1/.well-known/ready; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready on $port, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready in port ${port} after 120s"  
  exit 1
}

function wait_weaviates() {
  if [[ "$1" == "cluster" ]]; then
    wait_weaviate 8081
    wait_weaviate 8082
  fi
  wait_weaviate
}

function prepare_env() {
  echo_green "Preparing environment"
  echo "Download go deps"
  go mod tidy && go mod vendor
}

function upgrade_journey_test() {
  compose_type=$1
  echo_green "Upgrade journey test: $compose_type"
  echo "Start Weaviate v1.25.24"

  DOCKER_COMPOSE_VERSION=1.25.24 docker compose -f docker-compose-$compose_type.yml up -d
  wait_weaviates $compose_type

  echo "Create a class with nested properties using Weaviate v1.25"
  go test -count 1 -v -run TestCreateClass_v1_25 ./

  DOCKER_COMPOSE_VERSION=1.25.24 docker compose -f docker-compose-$compose_type.yml stop

  echo "Start Weaviate with fixed image"

  DOCKER_COMPOSE_VERSION=$weaviate_version docker compose -f docker-compose-$compose_type.yml up -d
  wait_weaviates $compose_type

  go test -count 1 -v -run TestRangeFiltersExists ./

  DOCKER_COMPOSE_VERSION=$weaviate_version docker compose -f docker-compose-$compose_type.yml stop
}

function upgrade_journey_with_fixed_image_test() {
  compose_type=$1
  echo_green "Upgrade journey test with fixed image in between: $compose_type"
  echo "Start Weaviate v1.25.24"

  DOCKER_COMPOSE_VERSION=1.25.24 docker compose -f docker-compose-$compose_type.yml up -d
  wait_weaviates $compose_type

  echo "Create a class with nested properties using Weaviate v1.25"
  go test -count 1 -v -run TestCreateClass_v1_25 ./

  DOCKER_COMPOSE_VERSION=1.25.24 docker compose -f docker-compose-$compose_type.yml stop

  echo "Start Weaviate v1.26.7 without a fix"

  DOCKER_COMPOSE_VERSION=1.26.7 docker compose -f docker-compose-$compose_type.yml up -d
  wait_weaviates $compose_type

  echo "Create a class with nested properties using Weaviate v1.25"
  go test -count 1 -v -run TestRangeFiltersDoesntExist ./

  DOCKER_COMPOSE_VERSION=1.26.7 docker compose -f docker-compose-$compose_type.yml stop

  echo "Start Weaviate with fixed image"

  DOCKER_COMPOSE_VERSION=$weaviate_version docker compose -f docker-compose-$compose_type.yml up -d
  wait_weaviates $compose_type

  go test -count 1 -v -run TestRangeFiltersExists ./

  DOCKER_COMPOSE_VERSION=$weaviate_version docker compose -f docker-compose-$compose_type.yml stop
}

echo_green "Starting journey tests"

prepare_env
upgrade_journey_test single
upgrade_journey_with_fixed_image_test single
upgrade_journey_test cluster
upgrade_journey_with_fixed_image_test cluster

echo_green "Success"
