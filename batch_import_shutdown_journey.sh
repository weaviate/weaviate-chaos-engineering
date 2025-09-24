#!/bin/bash

set -e

echo "Building all required containers"
( cd apps/batch-import-shutdown-journey/ && docker build -t batch_import_shutdown_journey . )

echo "Start the batch import journey"
container_id=$(docker run -d --network host -t batch_import_shutdown_journey python3 run.py)

echo "Wait a bit to let the import start"
shuf -i 1-10 -n 1 | xargs sleep # wait a bit before performing rolling restart

echo "Perform a rolling restart of the weaviate cluster"
kubectl rollout restart statefulset/weaviate -n weaviate

echo "Following the logs of the batch import journey"
docker logs -f "$container_id"
