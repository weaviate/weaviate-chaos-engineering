#!/bin/bash

set -e

function wait_weaviate_cluster() {
  echo "Wait for Weaviate to be ready"
  local node1_ready=false
  local node2_ready=false
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080/v1/.well-known/ready; then
      echo "Weaviate node1 is ready"
      node1_ready=true
    fi

    if curl -sf -o /dev/null localhost:8081; then
      echo "Weaviate node2 is ready"
      node2_ready=true
    fi

    if $node1_ready && $node2_ready; then
      return 0
    fi

    echo "Weaviate cluster is not ready, trying again in 1s"
    sleep 1
  done
  if ! $node1_ready; then
    echo "ERROR: Weaviate node1 is not ready after 120s"
  fi
  if ! $node2_ready; then
    echo "ERROR: Weaviate node2 is not ready after 120s"
  fi
  exit 1
}

echo "Building app container"
( cd apps/backup_and_restore_version_compatibility/ && docker build -t backup_and_restore_version_compatibility . )

echo "Generating version pairs"
cd apps/backup_and_restore_version_compatibility/ && docker build -f Dockerfile_gen_version_pairs \
    -t generate_version_pairs --build-arg weaviate_version=${WEAVIATE_VERSION} .
cd -

pair_string=$(docker run --rm generate_version_pairs 2>&1)
exit_code=$?
if [ $exit_code -ne 0 ]; then
  echo "ERROR: Failed to generate version pairs (exit code: $exit_code)"
  echo "Output: ${pair_string}"
  exit 1
fi

version_pairs=($pair_string)

# run backup/restore ops for each version pairing
for pair in "${!version_pairs[@]}"; do 
  backup_version=$(echo "${version_pairs[$pair]}" | cut -f1 -d+)
  restore_version=$(echo "${version_pairs[$pair]}" | cut -f2 -d+)

  export WEAVIATE_NODE_1_VERSION=$backup_version
  export WEAVIATE_NODE_2_VERSION=$restore_version

  export COMPOSE="apps/weaviate/docker-compose-backup.yml"

  echo "Starting Weaviate cluster..."
  docker compose -f $COMPOSE up -d weaviate-node-1 weaviate-backup-node backup-s3

  wait_weaviate_cluster

  echo "Creating S3 bucket..."
  docker compose -f $COMPOSE up create-s3-bucket

  echo "Run backup (v${backup_version}) and restore (v${restore_version}) version compatibility operations"
  docker run --rm --network host -t backup_and_restore_version_compatibility python3 backup_and_restore_version_compatibility.py

  echo "Removing S3 bucket..."
  docker compose -f $COMPOSE up remove-s3-bucket

  echo "Cleaning up containers for next test..."
  docker compose -f $COMPOSE down --remove-orphans
done

echo "Passed!"
