#!/bin/bash

set -e

SIZE=300000

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
  echo "Cleaning up ressources..."
  docker-compose -f apps/weaviate/docker-compose-replication.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker container rm -f importer &>/dev/null && echo 'Deleted container importer'
  docker container rm -f killer  &>/dev/null && echo 'Deleted container killer'
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Building all required containers"
( cd apps/replicated-import/ && docker build -t importer . )
( cd apps/chaotic-cluster-killer/ && docker build -t killer . )

echo "Starting Weaviate..."

docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Import schema"
if ! docker run \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  --rm \
  --name importer \
  -t importer python3 run.py --action schema; then
  echo "Could not apply schema"
  docker-compose -f apps/weaviate/docker-compose.yml logs
  exit 1
fi

echo "Starting the chaos script to kill Weaviate periodically (in the background)"
docker run \
  --network host \
  --rm -t -d \
  -e "WEAVIATE_VERSION=$WEAVIATE_VERSION" \
  -v "$PWD:$PWD" \
  -w "$PWD" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name killer \
  killer

echo "Run import script in foreground..."
if ! docker run \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  -t importer python3 run.py --action import; then
  echo "Importer failed, printing latest Weaviate logs..."
  docker-compose -f apps/weaviate/docker-compose.yml logs weaviate
  exit 1
fi

echo "Import completed successfully, stop killer"
docker rm -f killer

echo "Wait for Weaviate to be ready again in case there was a kill recently"
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Validate the count is correct"
object_count=$(curl -s 'localhost:8080/v1/graphql' -X POST \
  -H 'content-type: application/json' \
  -d '{"query":"{Aggregate{Document{meta{count}}}}"}' | \
  jq '.data.Aggregate.Document[0].meta.count')

if [ "$object_count" -lt "$SIZE" ]; then
  echo "Not enough objects present, wanted $SIZE, got $object_count"
  exit 1
fi

echo "Passed!"
shutdown
