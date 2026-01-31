#!/bin/bash

set -e

function logs() {
  echo "Showing logs:"
  services=$(docker compose -f "$COMPOSE" config --services)
  # Loop through each service
  for service in $services; do
    # Check if the service name starts with "weaviate"
    if [[ $service == weaviate* ]]; then
      # Fetch and print logs for the matching service
      docker compose -f "$COMPOSE" logs "$service"
    fi
  done
}

function report_container_state() {
  echo "======================================"
  echo "Docker Container Status Report"
  echo "======================================"

  if [ -n "$COMPOSE" ] && [ -f "$COMPOSE" ]; then
    echo "Checking containers for compose file: $COMPOSE"
    docker compose -f "$COMPOSE" ps -a
  else
    echo "No COMPOSE file specified or file not found"
  fi

  echo ""
  echo "All running Docker containers:"
  docker ps -a

  echo ""
  echo "Container resource usage:"
  docker stats --no-stream --no-trunc

  echo ""
  echo "Docker inspect for running containers:"
  running_containers=$(docker ps -q)
  if [ -n "$running_containers" ]; then
    docker inspect $running_containers
  else
    echo "No running containers to inspect"
  fi

  echo "======================================"
}

function wait_weaviate() {
  local port="${1:-8080}" # Set default port to 8080 if $1 is not provided
  local timeout="${2:-120}" # Set default timeout to 120 seconds if $2 is not provided
  echo "Wait for Weaviate to be ready"
  for ((i=1; i<=timeout; i++)); do
    if curl -sf -o /dev/null localhost:$port/v1/.well-known/ready; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready on $port, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready in port ${port} after ${timeout}s"
  exit 1
}

function shutdown() {
  echo "Cleaning up resources..."
  container_count=$(docker container ls -aq | wc -l)
  if [ "$container_count" -gt 0 ]; then
    # Place the command you want to execute here
    echo "There are $container_count containers."
    docker container rm -f $(docker container ls -aq)
  fi

  docker compose -f "$COMPOSE" down --remove-orphans

  # Try without sudo first, fall back to sudo if needed
  rm -rf apps/weaviate/data* 2>/dev/null || sudo rm -rf apps/weaviate/data* || true
  rm -rf workdir 2>/dev/null || sudo rm -rf workdir || true
}

trap 'logs; report_container_state; shutdown; exit 1' SIGINT ERR

trap 'exit_code=$?; if [[ $exit_code -eq 1 ]]; then logs; report_container_state; fi; shutdown' EXIT


function wait_for_indexing() {
  local weaviate_url="$1"
  local timeout="${2:-300}"
  local interval="${3:-5}"
  local check_after="${4:-0}"

  local nodes_url="${weaviate_url}/v1/nodes?output=verbose"

  if [[ $check_after -gt 0 ]]; then
    echo "Waiting ${check_after} seconds before starting checks..."
    sleep "$check_after"
  fi

  local start_time=$(date +%s)

  while true; do
    response=$(curl -s -f "$nodes_url")
    if [[ $? -ne 0 ]]; then
      echo "Error: Failed to fetch nodes data from $nodes_url"
      return 1
    fi

    # Check if all shards have vectorIndexingStatus set to READY
    all_ready=true
    shards_status=$(echo "$response" | jq -c '.nodes[]?.shards[]? | .vectorIndexingStatus' 2>/dev/null)
    
    if [[ $? -ne 0 || -z "$shards_status" ]]; then
      echo "Error: Unable to process shards data."
      return 1
    fi

    while read -r status; do
      if [[ "$status" != "\"READY\"" ]]; then
        all_ready=false
        echo "Shard with status $status is not ready yet."
        break
      fi
    done <<< "$shards_status"

    if $all_ready; then
      echo "All shards are READY."
      return 0
    fi

    current_time=$(date +%s)
    if (( current_time - start_time > timeout )); then
      echo "Timeout reached before all shards were READY."
      return 1
    fi

    echo "Some shards are not READY. Retrying in ${interval} seconds..."
    sleep "$interval"
  done
}


function get_env_vars() {
  PARTIAL_IMAGE_NAME="weaviate"
  CONTAINER_IDS=$(docker ps --filter "name=$PARTIAL_IMAGE_NAME" --format "{{.ID}}")
  if [ -z "$CONTAINER_IDS" ]; then
    echo "No running containers found matching image name: $PARTIAL_IMAGE_NAME"
    return 1
  fi
  for CONTAINER_ID in $CONTAINER_IDS; do
    echo "Fetching environment variables for container ID: $CONTAINER_ID"
    docker exec -i "$CONTAINER_ID" env
  done
  return 0
}