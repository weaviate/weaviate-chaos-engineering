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
  docker container rm -f corrupt-shards &>/dev/null && echo 'Deleted container corrupt-shards'
}
trap 'shutdown; exit 1' SIGINT ERR

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

whoami
pwd
ls -al .
ls -al apps/
ls -al apps/weaviate/
ls -al apps/weaviate/data-node-1/
ls -al apps/weaviate/data-node-1/pizza/
touch apps/weaviate/data-node-1/pizza/natee

echo DONEEE

exit 0

echo "Building all required containers"
( cd apps/corrupt-shards/ && docker build -t corrupt-shards . )

echo "Run setup"
docker run --rm --network host --name corrupt-shards-setup -t corrupt-shards setup

echo "Simulate corrupt shard"
docker compose -f apps/weaviate/docker-compose-replication.yml down
find apps/weaviate/data-node-1/pizza/*\
    -name 'segment-*.db' \
    -exec echo "truncating {}" \; \
    -exec truncate -s 0 "{}" \;
find apps/weaviate/data-node-1/pizza/*/main.hnsw.commitlog.d \
    -type f \
    -exec echo "moving {}" \; \
    -exec mv "{}" "{}.bak" \;
# "|| true" because using mv in find returns non-zero exit code
# find apps/weaviate/data-node-1/pizza \
#     -type d \
#     -d 1 \
#     -exec echo "moving {}" \; \
#     -exec mv "{}" "{}.bak" \; || true
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Run queries"
docker run --rm --network host --name corrupt-shards-query -t corrupt-shards query

echo "Success"
shutdown
