#!/bin/bash

set -e

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
( cd apps/ann-benchmarks/ && docker build -t ann_benchmarks . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up -d

wait_weaviate

echo "Run benchmark script"
mkdir -p datasets
( 
  cd datasets;
  if [ -f sift-128-euclidean.hdf5 ]
  then
      echo "Datasets exists locally"
  else
      echo "Downloading dataset"
      curl -LO http://ann-benchmarks.com/sift-128-euclidean.hdf5
  fi

)
docker run --network host -t -v "$PWD/datasets:/datasets" ann_benchmarks python3 run.py -v /datasets/sift-128-euclidean.hdf5 -d l2-squared -m 16 --compression

echo "Passed!"
