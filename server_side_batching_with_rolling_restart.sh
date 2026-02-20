#!/bin/bash

set -e

restart() {
    echo "wait a bit to let the imports start"
    shuf -i 5-20 -n 1 | xargs sleep # wait a bit before performing rolling restart

    echo "perform a rolling restart of the weaviate cluster"
    kubectl rollout restart statefulset/weaviate -n weaviate
}

follow_all() {
    local sync_id="$1" async_id="$2" ts_id="$3"

    docker logs -f "$sync_id" 2>&1 | sed "s/^/[sync] /" &
    docker logs -f "$async_id" 2>&1 | sed "s/^/[async] /" &
    docker logs -f "$ts_id" 2>&1 | sed "s/^/[typescript] /" &

    # Each monitor waits for its container; on failure it stops the others immediately
    monitor() {
        local name="$1" id="$2"
        shift 2
        local exit_code
        exit_code=$(docker wait "$id")
        if [ "$exit_code" -ne 0 ]; then
            echo "[$name] journey failed (exit $exit_code), stopping all journeys"
            docker stop "$@" 2>/dev/null || true
        fi
    }

    monitor "sync"       "$sync_id"  "$async_id" "$ts_id" &
    monitor "async"      "$async_id" "$sync_id"  "$ts_id" &
    monitor "typescript" "$ts_id"    "$sync_id"  "$async_id" &

    wait # wait for all monitors and log followers to finish

    # Report the root cause (first non-zero exit)
    for name_id in "sync:$sync_id" "async:$async_id" "typescript:$ts_id"; do
        local name="${name_id%%:*}" id="${name_id##*:}"
        local exit_code
        exit_code=$(docker inspect "$id" --format='{{.State.ExitCode}}')
        if [ "$exit_code" -ne 0 ]; then
            echo "$name journey failed"
            exit "$exit_code"
        fi
    done
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