#!/bin/bash

set +e  # Continue cleanup even if individual commands fail

echo "Running GCP resources cleanup..."

# Set timeout for gcloud commands
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
export CLOUDSDK_CORE_REQUEST_TIMEOUT=30

# Check if cleanup info directory exists
if [ ! -d ".cleanup_info" ]; then
  echo "No cleanup info found. Skipping cleanup."
  exit 0
fi

# Read cleanup variables from files
if [ -f ".cleanup_info/zone" ]; then
  zone=$(cat .cleanup_info/zone)
  echo "Using zone: $zone"
else
  echo "No zone info found. Skipping cleanup."
  exit 0
fi

# Function to run gcloud command with timeout
run_with_timeout() {
  local timeout_duration=$1
  shift
  timeout "$timeout_duration" "$@"
  return $?
}

# Cleanup instance
if [ -f ".cleanup_info/instance" ]; then
  instance=$(cat .cleanup_info/instance)
  echo "Deleting GCP instance: $instance"
  
  # Check if instance exists before trying to delete it (with timeout)
  echo "Checking if instance exists..."
  if run_with_timeout 30s gcloud compute instances describe "$instance" --zone="$zone" --format="value(name)" &>/dev/null; then
    echo "Instance exists, proceeding with deletion..."
    
    # Delete instance with timeout
    echo "Deleting instance..."
    if run_with_timeout 60s gcloud compute instances delete "$instance" --zone="$zone" --quiet; then
      echo "Delete command completed successfully"
    else
      echo "Delete command failed or timed out, but continuing..."
    fi
    
    # Wait for instance deletion with timeout
    echo "Waiting for instance to be deleted..."
    SECONDS=0
    timeout=120  # Reduced timeout
    while [ $SECONDS -lt $timeout ]; do
      if ! run_with_timeout 15s gcloud compute instances describe "$instance" --zone="$zone" --format="value(name)" &>/dev/null; then
        echo "Instance successfully deleted"
        break
      fi
      echo "Instance still exists, waiting... ($SECONDS/${timeout}s)"
      sleep 10
      SECONDS=$((SECONDS + 10))
    done
    
    if [ $SECONDS -ge $timeout ]; then
      echo "Warning: Timeout waiting for instance deletion confirmation."
      echo "The instance may still be deleting. Please check GCP console: $instance in zone $zone"
    fi
  else
    echo "Instance not found or describe command failed - assuming already deleted or doesn't exist"
  fi
else
  echo "No instance info found in cleanup files"
fi

# Clean up info files
rm -rf .cleanup_info

echo "GCP cleanup completed" 