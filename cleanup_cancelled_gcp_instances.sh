#!/bin/bash

set +e  # Continue cleanup even if individual commands fail

echo "Running cleanup for cancelled GCP instances..."

# Get the run ID
run_id=${GITHUB_RUN_ID:-""}
if [ -z "$run_id" ]; then
  echo "No GITHUB_RUN_ID found. Cannot proceed with cleanup."
  exit 0
fi

echo "Cleaning up instances for run ID: $run_id"

# Common zones used in the benchmarking scripts
zones=("us-central1-a" "us-central1-b" "us-central1-c" "us-east1-a" "us-west1-a")

# Find all instances that start with "benchmark-${run_id}-"
instance_prefix="benchmark-${run_id}-"
echo "Looking for instances with prefix: $instance_prefix"

instances_found=false

# Search in all zones
for zone in "${zones[@]}"; do
  echo "Checking zone: $zone"
  
  # Get list of instances matching the pattern in this zone
  instances=$(gcloud compute instances list --zones="$zone" --filter="name:${instance_prefix}*" --format="value(name)" 2>/dev/null || true)
  
  if [ -n "$instances" ]; then
    instances_found=true
    echo "Found instances in zone $zone:"
    echo "$instances"
    
    # Delete each instance
    for instance in $instances; do
      echo "Deleting instance: $instance in zone $zone"
      gcloud compute instances delete "$instance" --zone="$zone" --quiet || {
        echo "Failed to delete instance $instance in zone $zone, continuing..."
      }
      
      # Wait a bit to avoid API rate limits
      sleep 2
    done
  fi
done

if [ "$instances_found" = false ]; then
  echo "No instances found with prefix: $instance_prefix in any zone"
fi

echo "Cleanup for cancelled instances completed" 