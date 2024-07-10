#!/bin/bash

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

  echo killing $container_name now
    docker-compose -f apps/weaviate/docker-compose-replication.yml kill $container_name \
      && docker-compose -f apps/weaviate/docker-compose-replication.yml up -d $container_name

done

