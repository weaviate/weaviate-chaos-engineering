#!/bin/bash

set -e

ZONE=${ZONE:-"us-central1-a"}
MACHINE_TYPE=${MACHINE_TYPE:-"n2-standard-8"}
BOOT_DISK_SIZE=${BOOT_DISK_SIZE:-"10GB"}
MULTIVECTOR_DATASET=${MULTIVECTOR_DATASET:-"false"}
export CLOUD_PROVIDER="gcp"
export OS="ubuntu-2204-lts"

instance="benchmark-$(uuidgen | tr [:upper:] [:lower:])"

gcloud compute instances create $instance \
  --image-family=$OS --image-project=ubuntu-os-cloud \
  --machine-type=$MACHINE_TYPE --zone $ZONE \
  --boot-disk-size=$BOOT_DISK_SIZE

function cleanup {
  gcloud compute instances delete $instance --quiet --zone $ZONE
}
trap cleanup EXIT

# Busy loop to wait for SSH to be ready with a timeout of 5 minutes
echo "Waiting for SSH to be ready..."
SECONDS=0
timeout=300
while [ $SECONDS -lt $timeout ]; do
  if gcloud compute ssh --zone $ZONE $instance --command="echo SSH is ready" &>/dev/null; then
    break
  fi
  echo "SSH not ready, retrying in 5 seconds..."
  sleep 5
  SECONDS=$((SECONDS + 5))
done

if [ $SECONDS -ge $timeout ]; then
  echo "Timeout: VM is not SSH'able after 300 seconds"
  exit 1
fi

gcloud compute scp --zone $ZONE --recurse install_docker_ubuntu.sh "$instance:~"
gcloud compute ssh --zone $ZONE $instance -- 'sh install_docker_ubuntu.sh'
gcloud compute ssh --zone $ZONE $instance -- 'sudo sudo groupadd docker; sudo usermod -aG docker $USER'
gcloud compute ssh --zone $ZONE $instance -- "mkdir -p ~/apps/"
gcloud compute scp --zone $ZONE --recurse apps/ann-benchmarks "$instance:~/apps/"
gcloud compute scp --zone $ZONE --recurse apps/weaviate-no-restart-on-crash/ "$instance:~/apps/"
gcloud compute scp --zone $ZONE --recurse ann_benchmark_quantization.sh "$instance:~"
gcloud compute ssh --zone $ZONE $instance -- "MULTIVECTOR_DATASET=$MULTIVECTOR_DATASET DATASET=$DATASET DISTANCE=$DISTANCE REQUIRED_RECALL=$REQUIRED_RECALL QUANTIZATION=$QUANTIZATION WEAVIATE_VERSION=$WEAVIATE_VERSION MACHINE_TYPE=$MACHINE_TYPE CLOUD_PROVIDER=$CLOUD_PROVIDER OS=$OS bash ann_benchmark_quantization.sh"
mkdir -p results
gcloud compute scp --zone $ZONE --recurse "$instance:~/results/*.json" results/
