#!/bin/bash


function wait_weaviate() {
  echo "Wait for Weaviate to be ready on $1"
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

function shutdown() {
  echo "Cleaning up ressources..."
  docker compose -f apps/debug-reindexing-endpoint/docker-compose.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Starting Weaviate..."
docker compose -f apps/debug-reindexing-endpoint/docker-compose.yml up -d \
  weaviate-node-1 \
  weaviate-node-2 \
  weaviate-node-3

wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

cd ./apps/debug-reindexing-endpoint/ && go test  -timeout 3600s -v .

echo "Passed!"
shutdown
