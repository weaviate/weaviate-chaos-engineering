#!/bin/bash

set -e

port_to_query=$1
node_to_corrupt=$2
corruptions_to_apply=$3
consistency_level=$4
output_filepath=$5
# SUCCESS, WAIT, ERR
result="SUCCESS"
runtime="NULL"

start=`date +%s`

git submodule update --init --remote --recursive

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..15}; do
    if curl -sf -o /dev/null localhost:$1/v1/.well-known/ready; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready in port ${1} after 10s"
  result="WAIT"
  exit 2
}

function cleanup() {
  echo "Cleaning up ressources..."
  docker-compose -f apps/weaviate/docker-compose-replication.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker container rm -f corrupt-shards &>/dev/null && echo 'Deleted container corrupt-shards'
}

function shutdown() {
  # echo "Showing logs..."
  # docker-compose -f apps/weaviate/docker-compose-replication.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  cleanup
  echo "${WEAVIATE_VERSION},${port_to_query},${node_to_corrupt},${corruptions_to_apply},${consistency_level},${DISABLE_RECOVERY_ON_PANIC},${result},${runtime}" >> "${output_filepath}"
}
cleanup || true
trap 'result=ERR; shutdown; exit 1' SIGINT ERR

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Building all required containers"
( cd apps/corrupt-shards/ && docker build -t corrupt-shards . )

echo "Run setup"
docker run --rm --network host --name corrupt-shards-setup -t corrupt-shards setup "$port_to_query" "$consistency_level"

# TODO truncate stuff too, and change names
if [[ $corruptions_to_apply =~ "wal" ]]; then
  echo "Corrupting wal (and killing node)"
  # simulate crash so that *.wal files are not cleaned up
  docker kill "weaviate-weaviate-node-${node_to_corrupt}-1"
  find "apps/weaviate/data-node-${node_to_corrupt}/pizza" \
      -name 'segment-*.wal' \
      -exec echo "truncating {}" \; \
      -exec bash -c 'echo "natee" > "$1"' _ {} \;
fi
docker compose -f apps/weaviate/docker-compose-replication.yml down

if [ "$GITHUB_ACTIONS" = "true" ]; then
  # hack: 777 so i can "corrupt" docker volume files when running on github actions
  echo 'chmod on node volume'
  sudo chmod -R 777 "apps/weaviate/data-node-${node_to_corrupt}"
fi
if [[ $corruptions_to_apply =~ "hnsw_condensed_byte0" ]]; then
  echo "Corrupting byte0 of hnsw condensed files"
  # corrupt hnsw index by replacing the first byte, seems like it needs to be a pathological corruption?
  find "apps/weaviate/data-node-${node_to_corrupt}/pizza" \
      -name '*.condensed' \
      -exec echo "truncating {}" \; \
      -exec bash -c 'printf "X" | dd conv=notrunc seek=0 bs=1 count=1 of="$1"' _ {} \;
fi
if [[ $corruptions_to_apply =~ "segment_db_byte0" ]]; then
  echo "Corrupting byte0 of segment db files"
  find "apps/weaviate/data-node-${node_to_corrupt}/pizza" \
      -name 'segment-*.db' \
      -exec echo "truncating {}" \; \
      -exec bash -c 'printf "X" | dd conv=notrunc seek=0 bs=1 count=1 of="$1"' _ {} \;
fi
if [[ $corruptions_to_apply =~ "proplen_byte0" ]]; then
  echo "Corrupting byte0 of proplengths files"
  find "apps/weaviate/data-node-${node_to_corrupt}/pizza" \
      -name 'proplengths' \
      -exec echo "truncating {}" \; \
      -exec bash -c 'printf "X" | dd conv=notrunc seek=0 bs=1 count=1 of="$1"' _ {} \;
fi
# TODO other files/types of stuff/etc
if [[ $corruptions_to_apply =~ "raft_db_byte0" ]]; then
  echo "Corrupting byte0 of raft db files"
  find "apps/weaviate/data-node-${node_to_corrupt}" \
      -name 'raft.db' \
      -exec echo "truncating {}" \; \
      -exec bash -c 'xxd "$1" | head -n 1' _ {} \; \
      -exec truncate -s 1 "{}" \; \
      -exec bash -c 'xxd "$1" | head -n 1' _ {} \;
fi

# TODO change name of empty commitlog file (inc/dec by 42)?
# find apps/weaviate/data-node-1/pizza/*/main.hnsw.commitlog.d \
#     -type f \
#     -exec echo "moving {}" \; \
#     -exec mv "{}" "{}.foo" \;
# TODO do this in follow up test
# hack: added "|| true" because moving the shard dir via find/exec returns a non-zero exit code
# find apps/weaviate/data-node-1/pizza \
#     -type d \
#     -d 1 \
#     -exec echo "moving {}" \; \
#     -exec mv "{}" "{}.foo" \; || true
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Run queries"
docker run --rm --network host --name corrupt-shards-query -t corrupt-shards query "$port_to_query" "$consistency_level"

end=`date +%s`
runtime=$((end-start))
echo "Success"
shutdown
