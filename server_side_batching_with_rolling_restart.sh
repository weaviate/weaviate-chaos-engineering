#!/bin/bash

set -e

restart() {
    echo "wait a bit to let the import start"
    shuf -i 5-20 -n 1 | xargs sleep # wait a bit before performing rolling restart

    echo "perform a rolling restart of the weaviate cluster"
    kubectl rollout restart statefulset/weaviate -n weaviate
}

follow_all() {
    local sync_id="$1" async_id="$2" ts_id="$3"

    docker logs -f "$sync_id" 2>&1 | sed "s/^/[sync] /" &
    docker logs -f "$async_id" 2>&1 | sed "s/^/[async] /" &
    docker logs -f "$ts_id" 2>&1 | sed "s/^/[typescript] /" &

    sync_exit=$(docker wait "$sync_id")
    async_exit=$(docker wait "$async_id")
    ts_exit=$(docker wait "$ts_id")

    wait # flush remaining log output

    if [ "$sync_exit" -ne 0 ]; then echo "sync journey failed"; exit "$sync_exit"; fi
    if [ "$async_exit" -ne 0 ]; then echo "async journey failed"; exit "$async_exit"; fi
    if [ "$ts_exit" -ne 0 ]; then echo "typescript journey failed"; exit "$ts_exit"; fi
}

echo "building all required containers"
( cd apps/server-side-batching-with-rolling-restart/ && docker build -t server_side_batching_with_rolling_restart_py ./py && docker build -t server_side_batching_with_rolling_restart_ts ./ts )

echo "start the journeys"
sync_id=$(docker run -d --network host -t server_side_batching_with_rolling_restart_py python3 run.py sync)
async_id=$(docker run -d --network host -t server_side_batching_with_rolling_restart_py python3 run.py async)
ts_id=$(docker run -d --network host -t server_side_batching_with_rolling_restart_ts)

restart

follow_all "$sync_id" "$async_id" "$ts_id"

echo "All journeys completed successfully"