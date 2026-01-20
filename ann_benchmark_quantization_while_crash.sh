#!/bin/bash

set -e

dataset=${DATASET:-"sift-128-euclidean"}
distance=${DISTANCE:-"l2-squared"}
quantization=${QUANTIZATION:-"none"}
multivector=${MULTIVECTOR_DATASET:-"false"}
rq_bits=${RQ_BITS:-"8"}

wait_for_condensing() {
    local retries=300
    local attempt=1
    
    while [ $attempt -le $retries ]; do
        if docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml logs weaviate | grep "starting forced compaction"; then
            echo "starting forced compaction detected"
            return 0
        fi
        sleep 0.1
        attempt=$((attempt + 1))
    done

    echo "Failed to detect starting forced compaction after $retries attempts"
    return 1
}


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
echo "rq_bits: $rq_bits"
docker run --network host -t -v "$PWD/datasets:/datasets" -v "$PWD/results:/workdir/results" ann_benchmarks python3 run.py $multivector_flag -v /datasets/${dataset}.hdf5 -d $distance -m 16 --quantization $quantization --dim-to-segment-ratio 4 --rq-bits $rq_bits --labels "quantization=$quantization,after_restart=false,weaviate_version=$WEAVIATE_VERSION,cloud_provider=$CLOUD_PROVIDER,machine_type=$MACHINE_TYPE,os=$OS"

echo "Initial run complete, now restart Weaviate"
export HNSW_COMPACT_ON_STARTUP=true
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml stop weaviate
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml start weaviate

echo "Wait for the condensing to be started"
if ! wait_for_condensing; then
    exit 1
fi

echo "Restart the container while condensing"
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml kill weaviate \
    && docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml up weaviate -d

echo "Restart Weaviate again to ensure loading from new index"
export HNSW_COMPACT_ON_STARTUP=true
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml stop weaviate
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml start weaviate

wait_weaviate
echo "Weaviate ready, wait 30s for caches to be hot"
sleep 30

echo "Second run (query only)"
echo "try sleeping to reduce flakiness"
sleep 30
echo "done sleep"
docker run --network host -t -v "$PWD/datasets:/datasets" -v "$PWD/results:/workdir/results" ann_benchmarks python3 run.py $multivector_flag -v /datasets/${dataset}.hdf5 -d $distance -m 16 --quantization $quantization --query-only --labels "quantization=$quantization,after_restart=true,weaviate_version=$WEAVIATE_VERSION,cloud_provider=$CLOUD_PROVIDER,machine_type=$MACHINE_TYPE,os=$OS"

echo "Check if prefilled cache is used"
docker logs weaviate-no-restart-on-crash-weaviate-1 2>&1 | grep "prefilled"

echo "Check if completed loading shard"
docker logs weaviate-no-restart-on-crash-weaviate-1 2>&1 | grep -i "completed loading shard"

echo "Check if there are errors in the logs"
docker logs weaviate-no-restart-on-crash-weaviate-1 2>&1 | grep -i "erro"

docker run --network host -t -v "$PWD/datasets:/datasets" \
  -v "$PWD/results:/workdir/results" \
  -e "REQUIRED_RECALL=$REQUIRED_RECALL" \
  ann_benchmarks python3 analyze.py

echo "Passed!"
