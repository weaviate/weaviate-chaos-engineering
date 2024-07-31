#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$1/v1/.well-known/ready; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready in port ${1} after 120s"
  exit 1
}

echo "Building all required containers"
function shutdown() {
  echo "Cleaning up ressources..."
  docker compose -f apps/weaviate/docker-compose-single-voter-without-node-name.yml down --remove-orphans
  sudo rm -rf apps/weaviate/data* || true  
}
trap 'shutdown; exit 1' SIGINT ERR


export WEAVIATE_NODE_VERSION=1.25.0


echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-single-voter-without-node-name.yml up -d weaviate-node-1
wait_weaviate 8080

export WEAVIATE_NODE_VERSION=$WEAVIATE_VERSION

echo "Upgrade Weaviate..."
docker compose -f apps/weaviate/docker-compose-single-voter-without-node-name.yml up -d --force-recreate  weaviate-node-1
wait_weaviate 8080

echo "Success!"
shutdown
