#!/bin/bash

while true; do

  CONTAINER_ID=$(docker ps -qf 'name=weaviate')

  # assume container runs on host network, so we can simply contact weaviate
  # via its exposed port
  if ! curl -sf -o /dev/null localhost:8080; then
    echo "weaviate is not ready, postpone killing"
    sleep 3
    continue
  fi

  sleepsec=$(python3 -c "import random; print(random.randint(${SLEEP_START:=0},${SLEEP_END:=60}))")
  echo "waiting ${sleepsec}s for a kill"
  sleep "$sleepsec"

  echo killing now
  if [[ "${CHAOTIC_KILL_DOCKER}" == "y" ]]; then
    docker-compose -f apps/weaviate/docker-compose.yml kill weaviate \
      && docker-compose -f apps/weaviate/docker-compose.yml up weaviate -d
  else
    docker exec $CONTAINER_ID /bin/sh -c 'ps aux | grep '"'"'weaviate'"'"' | grep -v grep | awk '"'"'{print $1}'"'"' | xargs kill -9'
  fi

done

