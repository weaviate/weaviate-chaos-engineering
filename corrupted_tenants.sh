#!/bin/bash

set -e

git submodule update --init --remote --recursive

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

function shutdown() {
  echo "Showing logs..."
  docker-compose -f apps/weaviate/docker-compose-replication.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  echo "Cleaning up ressources..."
  docker-compose -f apps/weaviate/docker-compose-replication.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker container rm -f corrupted-tenants &>/dev/null && echo 'Deleted container corrupted-tenants'
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Building all required containers"
( cd apps/corrupted-tenants/ && docker build -t corrupted-tenants . )

# echo "Run script"
# docker run --network host --name corrupted-tenants -t corrupted-tenants

echo "Press any key to terminate the application..."

# Loop until a key is pressed
while true; do
read -rsn1 key  # Read a single character silently
if [[ -n "$key" ]]; then
echo "One key detected. Program terminated."
break  # Exit the loop if a key is pressed
fi
done

echo "Success"
shutdown
