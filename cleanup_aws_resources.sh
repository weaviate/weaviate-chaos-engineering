#!/bin/bash

set +e  # Continue cleanup even if individual commands fail

echo "Running AWS resources cleanup..."

# Check if cleanup info directory exists
if [ ! -d ".cleanup_info" ]; then
  echo "No cleanup info found. Skipping cleanup."
  exit 0
fi

# Read cleanup variables from files
if [ -f ".cleanup_info/region" ]; then
  region=$(cat .cleanup_info/region)
  echo "Using region: $region"
else
  echo "No region info found. Skipping cleanup."
  exit 0
fi

# Cleanup instance
if [ -f ".cleanup_info/instance_id" ]; then
  instance_id=$(cat .cleanup_info/instance_id)
  echo "Terminating instance $instance_id"
  aws ec2 terminate-instances --instance-ids "$instance_id" --region "$region" | jq || true

  echo "Waiting for instance to terminate..."
  SECONDS=0
  timeout=300
  while [ $SECONDS -lt $timeout ]; do
    status=$(aws ec2 describe-instances --instance-ids "$instance_id" --region "$region" 2>/dev/null | jq -r '.Reservations[0].Instances[0].State.Name' 2>/dev/null || echo "error")
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
    echo "Warning: Timeout waiting for instance termination. Please check AWS instances for manual cleanup."
  fi
fi

# Cleanup key pair
if [ -f ".cleanup_info/key_id" ]; then
  key_id=$(cat .cleanup_info/key_id)
  echo "Deleting key pair $key_id"
  aws ec2 delete-key-pair --key-name "$key_id" --region "$region" | jq || true
  rm -f "${key_id}.pem" || true
fi

# Cleanup security group
if [ -f ".cleanup_info/group_id" ]; then
  group_id=$(cat .cleanup_info/group_id)
  echo "Deleting security group $group_id"
  # Add retry loop for security group deletion since it might fail if instance is still terminating
  for i in {1..6}; do
    if aws ec2 delete-security-group --group-id "$group_id" --region "$region" 2>/dev/null | jq; then
      echo "Security group deleted successfully"
      break
    fi
    echo "Retrying security group deletion in 10 seconds... (attempt $i/6)"
    sleep 10
  done
fi

# Clean up info files
sudo rm -rf .cleanup_info || true

echo "AWS cleanup completed" 