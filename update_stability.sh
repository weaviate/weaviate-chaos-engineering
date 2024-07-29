#!/bin/bash

set -e

############################################
############# To test locally ##############
# export WEAVIATE_VERSION="1.26.1"
# export PERSISTENCE_LSM_ACCESS_STRATEGY="mmap"
# export PROMETHEUS_MONITORING_ENABLED="true"
# DISTANCE="cosine"
# INDEX_TYPE="hnsw"
# UPDATE_PERCENTAGE="0.08"
# CLEANUP_INTERVAL_SECONDS="30"
# UPDATE_ITERATIONS="10"
# REQUIRED_RECALL="0.992"
############################################

dataset=${DATASET:-"dbpedia-100k-openai-ada002-angular"}


echo "Starting Weaviate..."
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up -d


echo "Run benchmark script"
mkdir -p datasets
( 
  cd datasets;
  if [ -f ${dataset}.hdf5 ]
  then
      echo "Datasets exists locally"
  else
      echo "Downloading dataset"
      curl -LO https://storage.googleapis.com/ann-datasets/custom/${dataset}.hdf5
  fi

)

docker run --network host -t -v "$PWD/results:/app/results" -v "$PWD/datasets:/app/datasets" \
  -e "DATASET=$dataset" \
  -e "DISTANCE=$DISTANCE" \
  -e "INDEX_TYPE=$INDEX_TYPE" \
  -e "UPDATE_PERCENTAGE=$UPDATE_PERCENTAGE" \
  -e "CLEANUP_INTERVAL_SECONDS=$CLEANUP_INTERVAL_SECONDS" \
  -e "UPDATE_ITERATIONS=$UPDATE_ITERATIONS" \
  -e "REQUIRED_RECALL=$REQUIRED_RECALL" \
  -e "WEAVIATE_URL=localhost" \
  semitechnologies/weaviate-benchmarker:v2.0.0 /app/scripts/shell/update_stability.sh

echo "Stopping Weaviate..."
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml down

echo "Passed!"
