#!/bin/bash

# Copy of crash_restart in common.sh: this image only contains run.sh.
# crash_restart COMPOSE_FILE SERVICE: SIGKILL the service's container and start it
# again. `compose kill` can return before the container list `compose up` reads
# shows the container as exited; `up` then sees it "running" and starts nothing.
# So retry `up` until the container's start time changes.
function crash_restart() {
  local file="$1" service="$2" cid before
  cid=$(docker compose -f "$file" ps -q "$service")
  before=$(docker inspect -f '{{.State.StartedAt}}' "$cid")
  docker compose -f "$file" kill "$service"
  for attempt in 1 2 3 4 5; do
    docker compose -f "$file" up -d "$service"
    [ "$(docker inspect -f '{{.State.StartedAt}}' "$cid")" != "$before" ] && return 0
    echo "$service not restarted by up (attempt $attempt, state $(docker inspect -f '{{.State.Status}}' "$cid")), retrying in 1s"
    sleep 1
  done
  echo "ERROR: $service still not restarted after 5 attempts"
  return 1
}

while true; do

  # this script randomly kills either node 2 or node 3, in each iteration we
  # need to pick one
  port=8081
  container_name=$(python3 -c "import random; print(random.choice(['weaviate-node-2', 'weaviate-node-3']))")
  # container_id=$(docker ps -qf 'name=container_name')

  if [ "$container_name" == "weaviate-node-3" ]; then
    port=8082
  fi

  # assume container runs on host network, so we can simply contact weaviate
  # via its exposed port
  if ! curl -sf -o /dev/null localhost:$port; then
    echo "weaviate ($port) is not ready, postpone killing"
    sleep 3
    continue
  fi

  sleepsec=$(python3 -c "import random; print(random.randint(${SLEEP_START:=0},${SLEEP_END:=60}))")
  echo "waiting ${sleepsec}s for a kill of $container_name"
  sleep "$sleepsec"

  echo killing now
  crash_restart apps/weaviate/docker-compose-replication-static.yml "$container_name"

done

