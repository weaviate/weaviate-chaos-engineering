#!/bin/bash

set -e

echo "Building all required containers"
( cd apps/server-side-batching-with-rolling-restart/ && docker build -t server_side_batching_with_rolling_restart . )

echo "Start the sync journey"
container_id=$(docker run -d --network host -t server_side_batching_with_rolling_restart python3 run.py sync)

echo "Wait a bit to let the import start"
shuf -i 1-10 -n 1 | xargs sleep # wait a bit before performing rolling restart

echo "Perform a rolling restart of the weaviate cluster"
kubectl rollout restart statefulset/weaviate -n weaviate

echo "Following the logs of the journey"
docker logs -f "$container_id"

exit_code=$(docker inspect "$container_id" --format='{{.State.ExitCode}}')
echo "Sync container exited with code $exit_code"
if [ "$exit_code" -ne 0 ]; then
    echo "Sync journey failed"
    exit "$exit_code"
fi

echo "Start the async journey"
container_id=$(docker run -d --network host -t server_side_batching_with_rolling_restart python3 run.py async)

echo "Wait a bit to let the import start"
shuf -i 1-10 -n 1 | xargs sleep # wait a bit before performing rolling restart

echo "Perform a rolling restart of the weaviate cluster"
kubectl rollout restart statefulset/weaviate -n weaviate

echo "Following the logs of the journey"
docker logs -f "$container_id"

exit_code=$(docker inspect "$container_id" --format='{{.State.ExitCode}}')
echo "Async container exited with code $exit_code"
if [ "$exit_code" -ne 0 ]; then
    echo "Async journey failed"
    exit "$exit_code"
fi

echo "Both journeys completed successfully"