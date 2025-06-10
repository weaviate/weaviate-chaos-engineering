#!/bin/bash

set +e  # Continue cleanup even if individual commands fail

echo "Running AWS resources cleanup..."

# Set timeout for AWS CLI commands
export AWS_CLI_READ_TIMEOUT=30
export AWS_CLI_CONNECT_TIMEOUT=10
export AWS_MAX_ATTEMPTS=3

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

# Function to run AWS command with timeout
run_with_timeout() {
  local timeout_duration=$1
  shift
  timeout "$timeout_duration" "$@"
  return $?
}

# Cleanup instance
if [ -f ".cleanup_info/instance_id" ]; then
  instance_id=$(cat .cleanup_info/instance_id)
  echo "Terminating instance $instance_id"
  
  # Terminate instance with timeout
  if run_with_timeout 60s aws ec2 terminate-instances --instance-ids "$instance_id" --region "$region" --output json; then
    echo "Terminate command completed successfully"
  else
    echo "Terminate command failed or timed out, but continuing..."
  fi

  # Wait for instance termination with timeout
  echo "Waiting for instance to terminate..."
  SECONDS=0
  timeout=240  # Reduced timeout
  while [ $SECONDS -lt $timeout ]; do
    echo "Checking instance status... ($SECONDS/${timeout}s)"
    status=$(run_with_timeout 30s aws ec2 describe-instances --instance-ids "$instance_id" --region "$region" --output json 2>/dev/null | jq -r '.Reservations[0].Instances[0].State.Name' 2>/dev/null || echo "error")
    
    if [ "$status" = "terminated" ]; then
      echo "Instance successfully terminated"
      break
    elif [ "$status" = "error" ]; then
      echo "Instance not found or describe command failed - assuming terminated"
      break
    fi
    echo "Instance status: $status"
    sleep 10
    SECONDS=$((SECONDS + 10))
  done

  if [ $SECONDS -ge $timeout ]; then
    echo "Warning: Timeout waiting for instance termination confirmation."
    echo "The instance may still be terminating. Please check AWS console: $instance_id in region $region"
  fi
else
  echo "No instance info found in cleanup files"
fi

# Cleanup key pair
if [ -f ".cleanup_info/key_id" ]; then
  key_id=$(cat .cleanup_info/key_id)
  echo "Deleting key pair $key_id"
  
  if run_with_timeout 30s aws ec2 delete-key-pair --key-name "$key_id" --region "$region" --output json; then
    echo "Key pair deleted successfully"
  else
    echo "Key pair deletion failed or timed out"
  fi
  
  # Clean up local key file
  rm -f "${key_id}.pem" || true
else
  echo "No key pair info found in cleanup files"
fi

# Cleanup security group
if [ -f ".cleanup_info/group_id" ]; then
  group_id=$(cat .cleanup_info/group_id)
  echo "Deleting security group $group_id"
  
  # Add retry loop for security group deletion since it might fail if instance is still terminating
  for i in {1..6}; do
    echo "Attempting security group deletion (attempt $i/6)..."
    if run_with_timeout 30s aws ec2 delete-security-group --group-id "$group_id" --region "$region" --output json; then
      echo "Security group deleted successfully"
      break
    else
      if [ $i -lt 6 ]; then
        echo "Security group deletion failed or timed out, retrying in 10 seconds..."
        sleep 10
      else
        echo "Security group deletion failed after all attempts"
      fi
    fi
  done
else
  echo "No security group info found in cleanup files"
fi

# Clean up info files
rm -rf .cleanup_info

echo "AWS cleanup completed" 