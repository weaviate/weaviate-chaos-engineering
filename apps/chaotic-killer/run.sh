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

function wait_weaviate_until_schema() {
  while true; do
    RESP=$(curl -H 'content-type:application/json' http://localhost:8080/v1/schema)
    if [[ $RESP = "{\"classes\":[]}" ]]; then
      echo "weaviate does not have a schema yet, postpone killing"
      sleep 1
      continue
    else
      echo "schema found, proceed with killing"
      break
    fi
  done
}

function wait_weaviate() {
  while true; do
    if ! curl -sf -o /dev/null localhost:8080; then
      echo "weaviate is not ready, postpone killing"
      sleep 3
      continue
    else
      break
    fi
  done

  wait_weaviate_until_schema
}

while true; do

  CONTAINER_ID=$(docker ps -qf 'name=weaviate')

  # assume container runs on host network, so we can simply contact weaviate
  # via its exposed port
  wait_weaviate

  sleepsec=$(python3 -c "import random; print(random.randint(${SLEEP_START:=0},${SLEEP_END:=60}))")
  echo "waiting ${sleepsec}s for a kill"
  sleep "$sleepsec"

  echo killing now
  if [[ "${CHAOTIC_KILL_DOCKER}" == "y" ]]; then
    crash_restart apps/weaviate/docker-compose.yml weaviate
  else
    docker exec $CONTAINER_ID /bin/sh -c 'ps aux | grep '"'"'weaviate'"'"' | grep -v grep | awk '"'"'{print $1}'"'"' | xargs kill -9'
  fi

done
