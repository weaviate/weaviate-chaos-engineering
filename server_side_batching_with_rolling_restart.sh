#!/bin/bash

set -e

echo "Building all required containers"
( cd apps/server-side-batching-with-rolling-restart/py && docker build -t server_side_batching_with_rolling_restart . )

echo "Start the journey"
container_id=$(docker run -d --network host -t server_side_batching_with_rolling_restart python3 run.py sync)

echo "Wait a bit to let the import start"
shuf -i 5-20 -n 1 | xargs sleep # wait a bit before performing rolling restart

echo "Logs prior to restart"
docker logs -t "$container_id"

echo "Perform a rolling restart of the weaviate cluster"
kubectl rollout restart sts/weaviate -n weaviate

echo "Logs following the restart"
docker logs -t "$container_id"

echo "Show the pods restarting"
kubectl get pods -n weaviate

echo "Follow the logs until the journey completes"
docker logs -ft "$container_id"

exit_code=$(docker inspect "$container_id" --format='{{.State.ExitCode}}')
echo "Container exited with code $exit_code"
exit "$exit_code"