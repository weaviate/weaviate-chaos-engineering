#!/bin/bash 

set -e

gcloud config set compute/zone us-central1-a

instance="benchmark-$(uuidgen | tr [:upper:] [:lower:])"

gcloud compute instances create $instance \
  --image-family=ubuntu-2304-amd64 --image-project=ubuntu-os-cloud \
  --machine-type=c2-standard-8 

function cleanup {
  gcloud compute instances delete $instance --quiet
}
trap cleanup EXIT

echo "sleeping 30s for ssh to be ready"
sleep 30

gcloud compute ssh $instance -- "sudo apt-get update && sudo apt install -y docker.io docker-compose"
gcloud compute ssh $instance -- 'sudo sudo groupadd docker; sudo usermod -aG docker $USER'
gcloud compute ssh $instance -- "mkdir -p ~/apps/"
gcloud compute scp --recurse apps/ann-benchmarks "$instance:~/apps/"
gcloud compute scp --recurse apps/weaviate-no-restart-on-crash/ "$instance:~/apps/"
gcloud compute scp --recurse ann_benchmark_compression.sh "$instance:~"
gcloud compute ssh $instance -- "WEAVIATE_VERSION=$WEAVIATE_VERSION bash ann_benchmark_compression.sh"
gcloud compute scp --recurse "$instance:~/results/*.json" results/






