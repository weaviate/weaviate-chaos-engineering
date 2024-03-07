#!/bin/bash

set -e

git submodule update --init --recursive

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$1; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready in port ${1} after 120s"
  exit 1
}

function shutdown() {  
  docker logs weaviate-weaviate-node-1-1 2>&1 | grep error
  docker logs weaviate-weaviate-node-2-1 2>&1 | grep error
  docker logs weaviate-weaviate-node-3-1 2>&1 | grep error

  docker logs weaviate-weaviate-node-1-1 2>&1 | grep panic
  docker logs weaviate-weaviate-node-2-1 2>&1 | grep panic
  docker logs weaviate-weaviate-node-3-1 2>&1 | grep panic
  echo "Cleaning up ressources..."
  docker-compose -f apps/weaviate/docker-compose-replication.yml down --remove-orphans
  sudo rm -rf apps/weaviate/data* || true
  docker container rm -f multi-tenancy-activate-deactivate &>/dev/null && echo 'Deleted container multi-tenancy-activate-deactivate'
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Building all required containers"
( cd apps/multi-tenancy-activate-deactivate/ && docker build -t multi-tenancy-activate-deactivate . )

echo "Run script"
docker run --network host --name multi-tenancy-activate-deactivate -t multi-tenancy-activate-deactivate
echo "Success"
shutdown
