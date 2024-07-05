#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080/v1/.well-known/ready; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready after 120s"
  exit 1
}

function shutdown() {
  echo "Cleaning up ressources..."
  docker-compose -f apps/weaviate/docker-compose.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker rm -f rest-patch-stops-working-after-restart
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Building all required containers"
( cd apps/rest-patch-stops-working-after-restart/ && docker build -t rest-patch-stops-working-after-restart . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Run consecutive update operations"
docker run --network host -t rest-patch-stops-working-after-restart python3 rest-patch-stops-working-after-restart.py

echo "Restart Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml stop
docker-compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Run consecutive update operations after restart"
docker run --network host -t rest-patch-stops-working-after-restart python3 rest-patch-stops-working-after-restart.py

echo "Passed!"
shutdown
