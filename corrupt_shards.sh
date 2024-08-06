#!/bin/bash

set -e

port_to_query=$1
node_to_corrupt=$2
corruptions_to_apply=$3
consistency_level=$4
output_filepath=$5
# SUCCESS, WAIT, ERR
result="SUCCESS"
got_trap="FALSE"
runtime="NULL"

start=`date +%s`

git submodule update --init --remote --recursive

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  # TODO update to 60?
  for _ in {1..60}; do
    if curl -sf -o /dev/null localhost:$1/v1/.well-known/ready; then
      echo "Weaviate is ready"
      return 0
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
  echo "ERROR: Weaviate is not ready in port ${1} after 60s"
  result="WAIT"
  log_result
  exit 2
}

function log_result() {
  if [ "$result" != "WAIT" ] && [ "$got_trap" = "TRUE" ]; then
    result="ERR"
  fi
  echo "${WEAVIATE_VERSION},${port_to_query},${node_to_corrupt},${corruptions_to_apply},${consistency_level},${DISABLE_RECOVERY_ON_PANIC},${result},${runtime}" >> "${output_filepath}"
}

function cleanup() {
  echo "Cleaning up ressources..."
  docker compose -f apps/weaviate/docker-compose-replication.yml down --remove-orphans
  rm -rf apps/weaviate/data* || true
  docker container rm -f corrupt-shards &>/dev/null && echo 'Deleted container corrupt-shards'
}

function shutdown() {
  # echo "Showing logs..."
  docker compose -f apps/weaviate/docker-compose-replication.yml logs weaviate-node-1 weaviate-node-2 weaviate-node-3
  cleanup
  log_result
}
cleanup || true
trap 'got_trap="TRUE"; shutdown; exit 1' SIGINT ERR

echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Building all required containers"
( cd apps/corrupt-shards/ && docker build -t corrupt-shards . )

echo "Run setup"
docker run --rm --network host --name corrupt-shards-setup -t corrupt-shards setup "$port_to_query" "$consistency_level"

function overwrite() {
  find "$1" \
      -name "$2" \
      -exec echo "overwriting {}" \; \
      -exec bash -c 'echo "foo" > "$1"' _ {} \;
}

function byte0() {
  find "$1" \
      -name "$2" \
      -exec echo "corrupting byte0 of {}" \; \
      -exec bash -c 'printf "X" | dd conv=notrunc seek=0 bs=1 count=1 of="$1"' _ {} \;
}

function truncate1() {
  find "$1" \
      -name "$2" \
      -exec echo "truncating {} to len 1" \; \
      -exec truncate -s 1 "{}" \;
}

# function incrfilename() {
#   find "$1" \
#       -name "$2" \
#       -exec echo "incrementing filename of {}" \; \
#       -exec mv "{}" `expr {} + 42` \; \
# }

# TODO truncate stuff too, and change names
if [[ $corruptions_to_apply =~ "wal_overwrite" ]]; then
  echo "Corrupting wal (and killing node)"
  # simulate crash so that *.wal files are not cleaned up
  docker kill "weaviate-weaviate-node-${node_to_corrupt}-1"
  overwrite "apps/weaviate/data-node-${node_to_corrupt}/pizza" 'segment-*.wal'
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
  byte0 "apps/weaviate/data-node-${node_to_corrupt}/pizza" '*.condensed'
fi
if [[ $corruptions_to_apply =~ "classifications_byte0" ]]; then
  echo "corrupt byte0 of classifications db"
  byte0 "apps/weaviate/data-node-${node_to_corrupt}" 'classifications.db'
fi
if [[ $corruptions_to_apply =~ "modules_byte0" ]]; then
  echo "corrupt byte0 of modules db"
  byte0 "apps/weaviate/data-node-${node_to_corrupt}" 'modules.db'
fi
if [[ $corruptions_to_apply =~ "indexcount_byte0" ]]; then
  echo "corrupt byte0 of indexcount file"
  byte0 "apps/weaviate/data-node-${node_to_corrupt}" 'indexcount'
fi
if [[ $corruptions_to_apply =~ "segment_bloom_byte0" ]]; then
  echo "corrupt byte0 of boom files"
  byte0 "apps/weaviate/data-node-${node_to_corrupt}" 'segment-*.bloom'
fi
if [[ $corruptions_to_apply =~ "segment_cna_byte0" ]]; then
  echo "corrupt byte0 of cna files"
  byte0 "apps/weaviate/data-node-${node_to_corrupt}" 'segment-*.cna'
fi
if [[ $corruptions_to_apply =~ "segment_db_byte0" ]]; then
  echo "Corrupting byte0 of segment db files"
  byte0 "apps/weaviate/data-node-${node_to_corrupt}/pizza" 'segment-*.db'
fi
if [[ $corruptions_to_apply =~ "segment_wal_byte0" ]]; then
  echo "corrupt byte0 of segment wal files"
  byte0 "apps/weaviate/data-node-${node_to_corrupt}" 'segment-*.wal'
fi
if [[ $corruptions_to_apply =~ "proplen_byte0" ]]; then
  echo "Corrupting byte0 of proplengths file"
  byte0 "apps/weaviate/data-node-${node_to_corrupt}/pizza" 'proplengths'
fi
if [[ $corruptions_to_apply =~ "version_byte0" ]]; then
  echo "Corrupting byte0 of version file"
  byte0 "apps/weaviate/data-node-${node_to_corrupt}/pizza" 'version'
fi
if [[ $corruptions_to_apply =~ "schema_byte0" ]]; then
  echo "Corrupting byte0 of schema db"
  byte0 "apps/weaviate/data-node-${node_to_corrupt}/pizza" 'schema.db'
fi
# TODO files which dont have effect with byte0, try diff corruption?


if [[ $corruptions_to_apply =~ "raft_db_truncate1" ]]; then
  echo "truncate to len 1 for raft db files"
  truncate1 "apps/weaviate/data-node-${node_to_corrupt}" 'raft.db'
fi

# TODO do this in follow up test
# hack: added "|| true" because moving the shard dir via find/exec returns a non-zero exit code
# find apps/weaviate/data-node-1/pizza \
#     -type d \
#     -d 1 \
#     -exec echo "moving {}" \; \
#     -exec mv "{}" "{}.foo" \; || true

# TODO https://www.notion.so/weaviate/What-do-files-in-Weaviate-s-data-folder-do-6ac41928e85a437cb5784b0fe40f1864
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
