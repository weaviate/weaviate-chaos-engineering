#!/bin/bash

set -e

source common.sh

echo "Building all required containers"
( cd apps/server-side-batching-with-rolling-restart/ && docker build -t server_side_batching_with_rolling_restart . )
( cd apps/chaotic-killer/ && docker build -t killer . )

export COMPOSE="apps/weaviate/docker-compose-replication.yml"

echo "Starting Weaviate..."
docker compose -f $COMPOSE up -d

wait_weaviate

echo "Start the journey"
container_id=$(docker run -d --network host -t server_side_batching_with_rolling_restart python3 run.py)

echo "Wait a bit to let the import start"
shuf -i 1-10 -n 1 | xargs sleep # wait a bit before performing rolling restart

echo "Starting the chaos script to kill Weaviate periodically (in the background)"
docker run \
  --network host \
  --rm -t -d \
  -v "$PWD:$PWD" \
  -w "$PWD" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name killer \
  killer

echo "Following the logs of the journey"
docker logs -f "$container_id"

exit_code=$(docker inspect "$container_id" --format='{{.State.ExitCode}}')
echo "Container exited with code $exit_code"
exit "$exit_code"