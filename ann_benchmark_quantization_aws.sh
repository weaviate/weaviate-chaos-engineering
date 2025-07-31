#!/bin/bash

set -e

MACHINE_TYPE="${MACHINE_TYPE:-"m6i.2xlarge"}"
CLOUD_PROVIDER="aws"
OS="ubuntu-2204"
ARCH=amd64
dataset=${DATASET:-"sift-128-euclidean"}
distance=${DISTANCE:-"l2-squared"}
RQ_BITS=${RQ_BITS:-"8"}

region="eu-central-1"

# Generate deterministic run_id using GITHUB_RUN_ID + random string
github_run_id=${GITHUB_RUN_ID:-"local"}
random_suffix=$(head /dev/urandom | tr -dc a-z0-9 | head -c 8)
run_id="${github_run_id}-${random_suffix}"
key_id="key-$run_id"

# Create cleanup info directory and save region info
mkdir -p .cleanup_info
echo "$region" > .cleanup_info/region

vpc_id=$(aws ec2 describe-vpcs --region $region | jq -r '.Vpcs[0].VpcId')

# subnet_id=$( aws ec2 describe-subnets --region $region --filters=Name=vpc-id,Values=$vpc_id | jq -r '.Subnets[3].SubnetId')

group_id=$(aws ec2 create-security-group --group-name "benchmark-run-$run_id" --description "created for benchmark run $run_id" --vpc-id $vpc_id --region $region | jq -r '.GroupId'
)
# Save group_id for cleanup
echo "$group_id" > .cleanup_info/group_id

aws ec2 authorize-security-group-ingress --ip-permissions '[ { "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [ { "CidrIp": "0.0.0.0/0" } ] } ]' --group-id $group_id --region $region | jq

ami=$(aws ec2 describe-images --region $region --owner amazon --filter "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu*22.04*${ARCH}*" | jq -r '.Images[0].ImageId')

aws ec2 create-key-pair --key-name "$key_id" --region "$region" | jq -r '.KeyMaterial' > "${key_id}.pem"
chmod 600 "${key_id}.pem"
# Save key_id for cleanup
echo "$key_id" > .cleanup_info/key_id

instance_id=$(aws ec2 run-instances --image-id $ami --count 1 --instance-type $MACHINE_TYPE --key-name $key_id --security-group-ids $group_id  --region $region --associate-public-ip-address --cli-read-timeout 600 --ebs-optimized --block-device-mapping "[ { \"DeviceName\": \"/dev/sda1\", \"Ebs\": { \"VolumeSize\": 120 } } ]" | jq -r '.Instances[0].InstanceId' )

echo "instance ready: $instance_id"
# Save instance_id for cleanup
echo "$instance_id" > .cleanup_info/instance_id

function cleanup() {
  echo "Running cleanup via cleanup_aws_resources.sh..."
  bash ./cleanup_aws_resources.sh
}
trap cleanup EXIT SIGINT SIGTERM ERR

dns_name=
for i in {1..600}; do
  dns_name=$(aws ec2 describe-instances --filters "Name=instance-id,Values=$instance_id" --region $region | jq -r '.Reservations[0].Instances[0].PublicDnsName')

  if [ ! -z "$dns_name" ] && [ "$dns_name" != "null" ]; then
    if ssh-keyscan $dns_name > /dev/null; then
      break
    fi
  fi

  sleep 1
done

if [ -z "$dns_name" ] || [ "$dns_name" == "null" ]; then
  echo "did not receive dns name in time"
  exit 1
fi

ssh_addr="ubuntu@$dns_name"
echo "${key_id}.pem"
echo "$ssh_addr"

mkdir -p ~/.ssh
ssh-keyscan "$dns_name" >> ~/.ssh/known_hosts
echo "Added hosts"

# Busy loop to wait for the instance to be fully booted up with a timeout of 5 minutes
SECONDS=0
timeout=300
while [ $SECONDS -lt $timeout ]; do
  if ssh -i "${key_id}.pem" $ssh_addr -- 'echo "System is ready"'; then
    break
  fi
  sleep 1
  SECONDS=$((SECONDS + 1))
done

if [ $SECONDS -ge $timeout ]; then
  echo "Timeout: VM is not SSH'able after 300 seconds"
  exit 1
fi

scp -i "${key_id}.pem" -r install_docker_ubuntu.sh "$ssh_addr:~"
ssh -i "${key_id}.pem" $ssh_addr -- 'sh install_docker_ubuntu.sh'
ssh -i "${key_id}.pem" $ssh_addr -- 'sudo sudo groupadd docker; sudo usermod -aG docker $USER'
ssh -i "${key_id}.pem" $ssh_addr -- "mkdir -p ~/apps/"
scp -i "${key_id}.pem" -r apps/ann-benchmarks "$ssh_addr:~/apps/"
scp -i "${key_id}.pem" -r apps/weaviate-no-restart-on-crash/ "$ssh_addr:~/apps/"
scp -i "${key_id}.pem" -r ann_benchmark_quantization.sh "$ssh_addr:~"
ssh -i "${key_id}.pem" $ssh_addr -- "DATASET=$dataset DISTANCE=$distance REQUIRED_RECALL=$REQUIRED_RECALL QUANTIZATION=$QUANTIZATION WEAVIATE_VERSION=$WEAVIATE_VERSION MACHINE_TYPE=$MACHINE_TYPE CLOUD_PROVIDER=$CLOUD_PROVIDER OS=$OS RQ_BITS=$RQ_BITS bash ann_benchmark_quantization.sh"
mkdir -p results
scp -i "${key_id}.pem" -r "$ssh_addr:~/results/*.json" results/
