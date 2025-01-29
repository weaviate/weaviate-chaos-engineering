#!/bin/bash

set -e

MACHINE_TYPE="${MACHINE_TYPE:-"m6i.2xlarge"}"
CLOUD_PROVIDER="aws"
OS="ubuntu-2204"
ARCH=amd64
dataset=${DATASET:-"sift-128-euclidean"}
distance=${DISTANCE:-"l2-squared"}

region="eu-central-1"

# to make sure all aws resources are unique
run_id=$(uuidgen | tr [:upper:] [:lower:])
key_id="key-$run_id"

vpc_id=$(aws ec2 describe-vpcs --region $region | jq -r '.Vpcs[0].VpcId')

# subnet_id=$( aws ec2 describe-subnets --region $region --filters=Name=vpc-id,Values=$vpc_id | jq -r '.Subnets[3].SubnetId')

group_id=$(aws ec2 create-security-group --group-name "benchmark-run-$run_id" --description "created for benchmark run $run_id" --vpc-id $vpc_id --region $region | jq -r '.GroupId'
)
aws ec2 authorize-security-group-ingress --ip-permissions '[ { "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [ { "CidrIp": "0.0.0.0/0" } ] } ]' --group-id $group_id --region $region | jq

ami=$(aws ec2 describe-images --region $region --owner amazon --filter "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu*22.04*${ARCH}*" | jq -r '.Images[0].ImageId')

aws ec2 create-key-pair --key-name "$key_id" --region "$region" | jq -r '.KeyMaterial' > "${key_id}.pem"
chmod 600 "${key_id}.pem"

instance_id=$(aws ec2 run-instances --image-id $ami --count 1 --instance-type $MACHINE_TYPE --key-name $key_id --security-group-ids $group_id  --region $region --associate-public-ip-address --cli-read-timeout 600   --ebs-optimized --block-device-mapping "[ { \"DeviceName\": \"/dev/sda1\", \"Ebs\": { \"VolumeSize\": 120 } } ]" | jq -r '.Instances[0].InstanceId' )

echo "instance ready: $instance_id"

function cleanup() {
  set +e  # Continue cleanup even if individual commands fail

  if [ ! -z "$instance_id" ]; then
    echo "Terminating instance $instance_id"
    aws ec2 terminate-instances --instance-ids "$instance_id" --region "$region" | jq || true

    # Busy loop to wait for instance termination with timeout
    echo "Waiting for instance to terminate..."
    SECONDS=0
    timeout=300
    while [ $SECONDS -lt $timeout ]; do
      status=$(aws ec2 describe-instances --instance-ids "$instance_id" --region "$region" | jq -r '.Reservations[0].Instances[0].State.Name' || echo "error")
      if [ "$status" = "terminated" ]; then
        echo "Instance successfully terminated"
        break
      elif [ "$status" = "error" ]; then
        echo "Instance not found - assuming terminated"
        break
      fi
      echo "Instance status: $status"
      sleep 5
      SECONDS=$((SECONDS + 5))
    done

    if [ $SECONDS -ge $timeout ]; then
      echo "Error: Timeout waiting for instance termination. Please check AWS instances for manual cleanup."
      exit 1
    fi
  fi

  if [ ! -z "$key_id" ]; then
    echo "Deleting key pair $key_id"
    aws ec2 delete-key-pair --key-name "$key_id" --region "$region" | jq || true
    rm -f "${key_id}.pem" || true
  fi

  if [ ! -z "$group_id" ]; then
    echo "Deleting security group $group_id"
    # Add retry loop for security group deletion since it might fail if instance is still terminating
    for i in {1..6}; do
      if aws ec2 delete-security-group --group-id "$group_id" --region "$region" | jq; then
        break
      fi
      echo "Retrying security group deletion in 10 seconds..."
      sleep 10
    done
  fi
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
scp -i "${key_id}.pem" -r ann_benchmark.sh "$ssh_addr:~"
ssh -i "${key_id}.pem" $ssh_addr -- "DATASET=$dataset DISTANCE=$distance REQUIRED_RECALL=$REQUIRED_RECALL WEAVIATE_VERSION=$WEAVIATE_VERSION MACHINE_TYPE=$MACHINE_TYPE CLOUD_PROVIDER=$CLOUD_PROVIDER OS=$OS bash ann_benchmark.sh"
mkdir -p results
scp -i "${key_id}.pem" -r "$ssh_addr:~/results/*.json" results/
