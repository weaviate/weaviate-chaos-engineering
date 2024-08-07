#!/bin/bash

set -e

function logs() {
  echo "Showing logs:"
  compose_files=$(ls apps/weaviate/docker-compose-*.yml)
  for file in $compose_files; do
    services=$(docker compose -f "$file" config --services)
    # Loop through each service
    for service in $services; do
      # Check if the service name starts with "weaviate"
      if [[ $service == weaviate* ]]; then
        # Fetch and print logs for the matching service
        docker compose -f "$file" logs "$service"
      fi
    done
  done
}

function wait_weaviate() {
  local port="${1:-8080}" # Set default port to 8080 if $1 is not provided
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$port/v1/.well-known/ready; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready on $port, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready in port ${port} after 120s"  
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
    
  compose_files=$(ls apps/weaviate/docker-compose-*.yml)
  for file in $compose_files; do
    docker compose -f "$file" down --remove-orphans
  done
  
  rm -rf apps/weaviate/data* || true    
  rm -rf workdir
}
trap 'logs; shutdown; exit 1' SIGINT ERR