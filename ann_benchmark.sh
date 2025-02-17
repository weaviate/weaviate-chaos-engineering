#!/bin/bash

set -e

dataset=${DATASET:-"sift-128-euclidean"}
distance=${DISTANCE:-"l2-squared"}
multivector=${MULTIVECTOR_DATASET:-"false"}

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

echo "Building all required containers"
( cd apps/ann-benchmarks/ && docker build -t ann_benchmarks . )

echo "Starting Weaviate..."
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up -d

wait_weaviate

echo "Run benchmark script"
mkdir -p datasets
( 
  cd datasets;
  if [ -f ${dataset}.hdf5 ]
  then
      echo "Datasets exists locally"
  else
      echo "Downloading dataset"
      if [ "$multivector" = true ]; then

        echo "Downloading multivector dataset"
        curl -LO https://storage.googleapis.com/ann-datasets/custom/Multivector/${dataset}.hdf5
      else
        echo "Downloading single vector dataset"
        curl -LO http://ann-benchmarks.com/${dataset}.hdf5
      fi
  fi

)

if [ "$multivector" = true ]; then
  multivector_flag="-mv"
else
  multivector_flag=""
fi

docker run --network host -t -v "$PWD/results:/workdir/results" -v "$PWD/datasets:/datasets" ann_benchmarks python3 run.py $multivector_flag -v /datasets/${dataset}.hdf5 -d $distance -m 32 --labels "pq=false,after_restart=false,weaviate_version=$WEAVIATE_VERSION,cloud_provider=$CLOUD_PROVIDER,machine_type=$MACHINE_TYPE,os=$OS"

echo "Initial run complete, now restart Weaviate"

docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml stop weaviate
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml start weaviate

wait_weaviate
echo "Weaviate ready, wait 30s for caches to be hot"
sleep 30

echo "Second run (query only)"
docker run --network host -t -v "$PWD/results:/workdir/results" -v "$PWD/datasets:/datasets" ann_benchmarks python3 run.py $multivector_flag -v /datasets/${dataset}.hdf5 -d $distance -m 32 --query-only --labels "pq=false,after_restart=true,weaviate_version=$WEAVIATE_VERSION,cloud_provider=$CLOUD_PROVIDER,machine_type=$MACHINE_TYPE,os=$OS"

docker run --network host -t -v "$PWD/datasets:/datasets" \
  -v "$PWD/results:/workdir/results" \
  -e "REQUIRED_RECALL=$REQUIRED_RECALL" \
  ann_benchmarks python3 analyze.py


echo "Passed!"
