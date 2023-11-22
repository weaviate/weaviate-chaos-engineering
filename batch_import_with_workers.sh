#!/bin/bash


set -e

dataset=${DATASET:-"sift-128-euclidean"}
distance=${DISTANCE:-"l2-squared"}

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080; then
      echo "Weaviate is ready"
      break
    fi

    echo "Weaviate is not ready, trying again in 1s"
    sleep 1
  done
}

echo "Building all required containers"
( cd apps/batch-import-with-workers/ && docker build -t batch_import_with_workers . )

echo "Starting Weaviate..."
export ASYNC_INDEXING=true
docker compose -f apps/weaviate/docker-compose-async.yml up -d

wait_weaviate

echo "Download dataset"
mkdir -p datasets
( 
  cd datasets;
  if [ -f ${dataset}.hdf5 ]
  then
      echo "Datasets exists locally"
  else
      echo "Downloading dataset"
      curl -LO http://ann-benchmarks.com/${dataset}.hdf5
  fi

)
echo "Run import script with workers (ASYNC_INDEXING=$ASYNC_INDEXING)"
docker run --network host -t -v "$PWD/results:/workdir/results" -v "$PWD/datasets:/datasets" batch_import_with_workers python3 run.py -v /datasets/${dataset}.hdf5 -d $distance -m 32 --labels "pq=false,weaviate_version=$WEAVIATE_VERSION,cloud_provider=$CLOUD_PROVIDER,machine_type=$MACHINE_TYPE,os=$OS"

echo "Initial run complete, now restart Weaviate with async indexing ON"

docker compose -f apps/weaviate-no-restart-on-crash/docker-compose-async.yml stop weaviate
export ASYNC_INDEXING=false
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose-async.yml start weaviate

echo "Second run with workers (ASYNC_INDEXING=$ASYNC_INDEXING)"
docker run --network host -t -v "$PWD/results:/workdir/results" -v "$PWD/datasets:/datasets" batch_import_with_workers python3 run.py -v /datasets/${dataset}.hdf5 -d $distance -m 32 --labels "pq=false,weaviate_version=$WEAVIATE_VERSION,cloud_provider=$CLOUD_PROVIDER,machine_type=$MACHINE_TYPE,os=$OS"

docker run --network host -t -v "$PWD/datasets:/datasets" \
  -v "$PWD/results:/workdir/results" \
  -e "REQUIRED_RECALL=$REQUIRED_RECALL" \
  batch_import_with_workers python3 analyze.py


echo "Passed!"
