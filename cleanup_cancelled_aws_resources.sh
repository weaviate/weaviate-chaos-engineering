#!/bin/bash

set +e  # Continue cleanup even if individual commands fail

echo "Running cleanup for cancelled AWS resources..."

# Get the run ID
github_run_id=${GITHUB_RUN_ID:-""}
if [ -z "$github_run_id" ]; then
  echo "No GITHUB_RUN_ID found. Cannot proceed with cleanup."
  exit 0
fi

echo "Cleaning up resources for run ID: $github_run_id"

# Default region used in the benchmarking scripts
region="eu-central-1"

# Find all security groups and instances that start with our run ID
run_id_prefix="${github_run_id}-"
echo "Looking for resources with run ID prefix: $run_id_prefix"

resources_found=false

# Find and terminate instances
echo "Looking for instances with security groups named 'benchmark-run-${run_id_prefix}*'"
instances=$(aws ec2 describe-instances \
  --region "$region" \
  --filters "Name=instance-state-name,Values=running,pending,stopped,stopping" \
  --query "Reservations[*].Instances[?starts_with(SecurityGroups[0].GroupName, 'benchmark-run-${run_id_prefix}')].InstanceId" \
  --output text 2>/dev/null || true)

if [ -n "$instances" ] && [ "$instances" != "None" ]; then
  resources_found=true
  echo "Found instances to terminate:"
  echo "$instances"
  
  for instance_id in $instances; do
    echo "Terminating instance: $instance_id"
    aws ec2 terminate-instances --instance-ids "$instance_id" --region "$region" || {
      echo "Failed to terminate instance $instance_id, continuing..."
    }
  done
  
  # Wait for instances to terminate before cleaning up other resources
  echo "Waiting for instances to terminate..."
  aws ec2 wait instance-terminated --instance-ids $instances --region "$region" || {
    echo "Timeout waiting for instances to terminate, continuing with cleanup..."
  }
fi

# Find and delete security groups
echo "Looking for security groups named 'benchmark-run-${run_id_prefix}*'"
security_groups=$(aws ec2 describe-security-groups \
  --region "$region" \
  --filters "Name=group-name,Values=benchmark-run-${run_id_prefix}*" \
  --query "SecurityGroups[].GroupId" \
  --output text 2>/dev/null || true)

if [ -n "$security_groups" ] && [ "$security_groups" != "None" ]; then
  resources_found=true
  echo "Found security groups to delete:"
  echo "$security_groups"
  
  for group_id in $security_groups; do
    echo "Deleting security group: $group_id"
    aws ec2 delete-security-group --group-id "$group_id" --region "$region" || {
      echo "Failed to delete security group $group_id, continuing..."
    }
    sleep 2
  done
fi

# Find and delete key pairs
echo "Looking for key pairs named 'key-${run_id_prefix}*'"
key_pairs=$(aws ec2 describe-key-pairs \
  --region "$region" \
  --filters "Name=key-name,Values=key-${run_id_prefix}*" \
  --query "KeyPairs[].KeyName" \
  --output text 2>/dev/null || true)

if [ -n "$key_pairs" ] && [ "$key_pairs" != "None" ]; then
  resources_found=true
  echo "Found key pairs to delete:"
  echo "$key_pairs"
  
  for key_name in $key_pairs; do
    echo "Deleting key pair: $key_name"
    aws ec2 delete-key-pair --key-name "$key_name" --region "$region" || {
      echo "Failed to delete key pair $key_name, continuing..."
    }
    # Also remove local pem file if it exists
    rm -f "${key_name}.pem" || true
  done
fi

if [ "$resources_found" = false ]; then
  echo "No AWS resources found with run ID prefix: $run_id_prefix"
fi

echo "Cleanup for cancelled AWS resources completed" 