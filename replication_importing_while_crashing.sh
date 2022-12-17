#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$1; then
      echo "Weaviate is ready"
      break
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
}

echo "Building all required containers"
( cd apps/replicated-import/ && docker build -t importer . )
( cd apps/chaotic-cluster-killer/ && docker build -t killer . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1
wait_weaviate 8080
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-2
wait_weaviate 8081
docker-compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-3
wait_weaviate 8082

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
  -t importer python3 run.py ; then
  echo "Importer failed, printing latest Weaviate logs..."
  docker-compose -f apps/weaviate/docker-compose.yml logs weaviate
  exit 1
fi

echo "Import completed successfully, stop killer"
docker rm -f killer

# echo "Wait for Weaviate to be ready again in case there was a kill recently"
# wait_weaviate

# echo "Validate the count is correct"
# object_count=$(curl -s 'localhost:8080/v1/graphql' -X POST \
#   -H 'content-type: application/json' \
#   -d '{"query":"{Aggregate{DemoClass{meta{count}}}}"}' | \
#   jq '.data.Aggregate.DemoClass[0].meta.count')

# if [ "$object_count" -lt "$SIZE" ]; then
#   echo "Not enough objects present, wanted $SIZE, got $object_count"
#   exit 1
# fi

echo "Passed!"
