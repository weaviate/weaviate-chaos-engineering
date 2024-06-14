#!/bin/bash

set -e

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080; then
      echo "Weaviate is ready"
      break
    fi

    echo "Weaviate is not ready, trying again in 1s"
    sleep 1
  done
}

echo "Building all required containers"
( cd apps/create-class-validation && docker build -t create-class-validation . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml up -d

wait_weaviate

echo "Run class vaidation tests - invalid longname"
docker run --network host -t create-class-validation python3 invalid_class_long_name.py

echo "Passed!"

if curl -sf -o /dev/null localhost:8080; then
  echo "weaviate is running"
  PID=$(lsof -ti :8080)
  echo "stopping weaviate application"
  kill $PID
  echo "weaviate application is stopped"
else
  echo "weaviate has not stopped"
fi

echo "Starting Weaviate with auto-schema enabled..."
docker-compose -f apps/create-class-validation/docker-compose.yml up -d

docker run --network host -t create-class-validation python3 invalid_class_long_name.py

echo "Passed!"
