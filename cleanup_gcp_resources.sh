#!/bin/bash

set +e  # Continue cleanup even if individual commands fail

echo "Running GCP resources cleanup..."

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

# Cleanup instance
if [ -f ".cleanup_info/instance" ]; then
  instance=$(cat .cleanup_info/instance)
  echo "Deleting GCP instance: $instance"
  
  # Check if instance exists before trying to delete it
  if gcloud compute instances describe "$instance" --zone="$zone" &>/dev/null; then
    echo "Instance exists, proceeding with deletion..."
    gcloud compute instances delete "$instance" --zone="$zone" --quiet || true
    
    # Wait for instance deletion with timeout
    echo "Waiting for instance to be deleted..."
    SECONDS=0
    timeout=180
    while [ $SECONDS -lt $timeout ]; do
      if ! gcloud compute instances describe "$instance" --zone="$zone" &>/dev/null; then
        echo "Instance successfully deleted"
        break
      fi
      echo "Instance still exists, waiting..."
      sleep 5
      SECONDS=$((SECONDS + 5))
    done
    
    if [ $SECONDS -ge $timeout ]; then
      echo "Warning: Timeout waiting for instance deletion. Please check GCP console for manual cleanup."
    fi
  else
    echo "Instance not found - assuming already deleted"
  fi
fi

# Clean up info files
rm -rf .cleanup_info

echo "GCP cleanup completed" 