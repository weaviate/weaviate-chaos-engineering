#!/bin/bash

set -e

restart() {
    echo "wait a bit to let the import start"
    shuf -i 1-10 -n 1 | xargs sleep # wait a bit before performing rolling restart

    echo "perform a rolling restart of the weaviate cluster"
    kubectl rollout restart statefulset/weaviate -n weaviate

    echo "following the logs of the journey"
    docker logs -f "$1"

    exit_code=$(docker inspect "$1" --format='{{.State.ExitCode}}')
    echo "container exited with code $exit_code"
    if [ "$exit_code" -ne 0 ]; then
        echo "$2 journey failed"
        exit "$exit_code"
    fi
}

echo "building all required containers"
( cd apps/server-side-batching-with-rolling-restart/ && docker build -t server_side_batching_with_rolling_restart_py ./py && docker build -t server_side_batching_with_rolling_restart_ts ./ts )

echo "start the sync python journey"
container_id=$(docker run -d --network host -t server_side_batching_with_rolling_restart_py python3 run.py sync)
restart "$container_id" "sync python"

echo "start the async python journey"
container_id=$(docker run -d --network host -t server_side_batching_with_rolling_restart_py python3 run.py async)
restart "$container_id" "async python"

echo "start the typescript journey"
container_id=$(docker run -d --network host -t server_side_batching_with_rolling_restart_ts)
restart "$container_id" "typescript"

echo "All journeys completed successfully"