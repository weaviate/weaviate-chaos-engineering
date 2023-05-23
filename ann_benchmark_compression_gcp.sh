#!/bin/bash 

set -e

gcloud config set compute/zone us-central1-a

export MACHINE_TYPE=c2-standard-8
export CLOUD_PROVIDER=gcp
export OS=ubuntu-2304-amd64

instance="benchmark-$(uuidgen | tr [:upper:] [:lower:])"

gcloud compute instances create $instance \
  --image-family=$OS --image-project=ubuntu-os-cloud \
  --machine-type=$MACHINE_TYPE

function cleanup {
  gcloud compute instances delete $instance --quiet
}
trap cleanup EXIT

echo "sleeping 30s for ssh to be ready"
sleep 30

gcloud compute scp --recurse install_docker_ubuntu.sh "$instance:~"
gcloud compute ssh $instance -- 'sh install_docker_ubuntu.sh'
gcloud compute ssh $instance -- 'sudo sudo groupadd docker; sudo usermod -aG docker $USER'
gcloud compute ssh $instance -- "mkdir -p ~/apps/"
gcloud compute scp --recurse apps/ann-benchmarks "$instance:~/apps/"
gcloud compute scp --recurse apps/weaviate-no-restart-on-crash/ "$instance:~/apps/"
gcloud compute scp --recurse ann_benchmark_compression.sh "$instance:~"
gcloud compute ssh $instance -- "WEAVIATE_VERSION=$WEAVIATE_VERSION MACHINE_TYPE=$MACHINE_TYPE CLOUD_PROVIDER=$CLOUD_PROVIDER OS=$OS bash ann_benchmark_compression.sh"
gcloud compute scp --recurse "$instance:~/results/*.json" results/






