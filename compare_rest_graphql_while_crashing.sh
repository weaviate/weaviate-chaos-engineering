#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/compare-rest-graphql/ && docker build -t compare-rest-graphql . )
( cd apps/chaotic-killer/ && docker build -t killer . )

export COMPOSE="apps/weaviate/docker-compose.yml"
echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate

echo "Starting the chaos script to kill Weaviate periodically (in the background)"
docker run \
  --network host \
  --rm -t-d \
  -v "$PWD:$PWD" \
  -e "SLEEP_START=2" \
  -e "SLEEP_END=5" \
  -e "WEAVIATE_VERSION=${WEAVIATE_VERSION}" \
  -e "CHAOTIC_KILL_DOCKER=y" \
  -w "$PWD" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name killer \
  killer

echo "Run compare script in foreground..."
if ! docker run \
  -e "MAX_ATTEMPTS=100" \
   --network host -t compare-rest-graphql python3 objects-are-not-deleted.py 0; then
  echo "Discrepancy error!"
  echo "Stopping chaotic killer"
  docker rm -f killer
  exit 1
fi

echo "Script completed successfully, stop killer"
docker rm -f killer

echo "Passed!"
shutdown
