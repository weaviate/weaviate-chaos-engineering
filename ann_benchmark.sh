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
  if [ -f ${dataset}.hdf5 ] && [ -s ${dataset}.hdf5 ]
  then
      echo "Dataset exists locally and is not empty"
  else
      echo "Downloading dataset"
      # Add retries and more robust download
      for i in {1..3}; do
        echo "Download attempt $i"
        rm -f ${dataset}.hdf5.tmp
        if [ "$multivector" = true ]; then
  
          echo "Downloading multivector dataset"
          curl -LO https://storage.googleapis.com/ann-datasets/custom/Multivector/${dataset}.hdf5
          break
        else
          echo "Downloading single vector dataset"
          if curl -L --retry 5 --retry-delay 2 --connect-timeout 30 -o ${dataset}.hdf5.tmp http://ann-benchmarks.com/${dataset}.hdf5; then
            # Verify file is not empty
            if [ -s ${dataset}.hdf5.tmp ]; then
              mv ${dataset}.hdf5.tmp ${dataset}.hdf5
              echo "Download successful"
              break
            else
              echo "Downloaded file is empty, retrying..."
            fi
          else
            echo "Download failed, retrying..."
          fi
        fi

        if [ $i -eq 3 ]; then
          echo "Failed to download dataset after 3 attempts"
          exit 1
        fi

        sleep 5
      done
  fi

  # Verify the file exists and is not empty
  if [ ! -f ${dataset}.hdf5 ] || [ ! -s ${dataset}.hdf5 ]; then
    echo "Dataset file is missing or empty"
    exit 1
  fi

  echo "Dataset file size: $(du -h ${dataset}.hdf5)"
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
echo "Weaviate ready, wait 180s for caches to be hot"
sleep 180

echo "Second run (query only)"
docker run --network host -t -v "$PWD/results:/workdir/results" -v "$PWD/datasets:/datasets" ann_benchmarks python3 run.py $multivector_flag -v /datasets/${dataset}.hdf5 -d $distance -m 32 --query-only --labels "pq=false,after_restart=true,weaviate_version=$WEAVIATE_VERSION,cloud_provider=$CLOUD_PROVIDER,machine_type=$MACHINE_TYPE,os=$OS"

docker run --network host -t -v "$PWD/datasets:/datasets" \
  -v "$PWD/results:/workdir/results" \
  -e "REQUIRED_RECALL=$REQUIRED_RECALL" \
  ann_benchmarks python3 analyze.py


echo "Passed!"
