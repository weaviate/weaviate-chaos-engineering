#!/bin/bash

set -e

source common.sh

SIZE=10000
BATCH_SIZE=1024

# This test is to check if the compaction optimization is working as expected,
# by forcing the maximum segment size to be a low number, 5MB, we ensure that
# the compaction continues even if the first segment size is over that limit.
# We noticed in one of our customers that the lsm store had grown massively
# and the compaction was not working as expected, this test is to ensure that
# the compaction is working as expected.

echo "Building all required containers"
( cd apps/importer-no-vector-index/ && docker build -t importer-no-vector . )
( cd apps/analyze-segments/ && docker build -t analyzer . )

export COMPOSE="apps/weaviate-no-restart-on-crash/docker-compose.yml"

echo "Starting Weaviate..."
PERSISTENCE_MEMTABLES_FLUSH_IDLE_AFTER_SECONDS=1 PERSISTENCE_LSM_MAX_SEGMENT_SIZE="5MB" docker compose -f $COMPOSE up -d

wait_weaviate

function dump_logs() {
  docker compose -f $COMPOSE logs
}

trap 'dump_logs' ERR

echo "Run import script in foreground..."
if ! docker run \
  -e 'SHARDS=1' \
  -e "SIZE=$SIZE" \
  -e "BATCH_SIZE=$BATCH_SIZE" \
  -e 'ORIGIN=http://localhost:8080' \
  --network host \
  -t importer-no-vector; then
  echo "Importer failed, printing latest Weaviate logs..."  
    exit 1
fi

class_name="novector"
echo "Run analize segments script"
dir=$(ls --color=never ./apps/weaviate/data/${class_name})
num_directories=$(echo "$dir" | wc -l)
if [ "$num_directories" -gt 1 ]; then
  echo "Error: Multiple directories found in ./apps/weaviate/data/${class_name}"
  echo "$dir"
  exit 1
fi

echo "Segments analysis with max LSM segment size of 5MB:"
output=$(docker run --network host -v ./apps/weaviate/data/${class_name}/${dir}/lsm/objects:/lsm_objects -t analyzer /app/analyzer --path /lsm_objects)
echo "$output"

# Maximum segment size
new_size=15

# Restart Weaviate and increase the max segment size to  ${new_size} MB
docker compose -f apps/weaviate-no-restart-on-crash/docker-compose.yml down
PERSISTENCE_MEMTABLES_FLUSH_IDLE_AFTER_SECONDS=1 PERSISTENCE_LSM_MAX_SEGMENT_SIZE="${new_size}MB" docker compose -f $COMPOSE up -d

echo "Checking segment levels after max LSM segment increased to ${new_size}MB..."
# Wait for the compaction to occurr by checking the number of segments every 3 seconds.
# If the number of segments is decreasing, it means the compaction is ongoing. If the number of segments
# is the same for 5 consecutive checks, it means the compaction is finished.
timeout=120
start_time=$(date +%s)
prev_count=0
same_count=0
while true; do
  output=$(docker run --network host -v ./apps/weaviate/data/${class_name}/${dir}/lsm/objects:/lsm_objects -t analyzer /app/analyzer --path /lsm_objects)
  
  # Extract levels from the output
  levels=$(awk 'NR>3 {print $3}' <<< "$output")
  levels_count=$(echo "$levels" | wc -l)
  
  # Check if levels_count is decreasing
  if [ $levels_count -lt $prev_count ]; then
    same_count=0
  else
    same_count=$((same_count + 1))
  fi
  
  # If for 5 consecutive checks the number of segments is the same, break the loop
  if [ $same_count -ge 5 ]; then
    echo "Compaction finished. All segments were compacted."
    break
  fi
  
  prev_count=$levels_count
  
  echo "Compaction ongoing. Waiting..."
  sleep 3
done
echo "$output"
echo ""

# Once all segments are in compacted check if the sum of any pair of segments is greater than ${new_size}MB
# for consecutive pairs of segments with the same level.
output=$(docker run --network host -v ./apps/weaviate/data/${class_name}/${dir}/lsm/objects:/lsm_objects -t analyzer /app/analyzer --path /lsm_objects)

# Process the output, extracting segment sizes and levels
segments=$(awk 'NR>3 {print $2, $3}' <<< "$output")
segment_sizes=($(awk '{print $1}' <<< "$segments"))
segment_levels=($(awk '{print $2}' <<< "$segments"))

# Get total segments count
total_segments=${#segment_sizes[@]}

# Iterate over the segments and compare pairs
for ((i=0; i<$total_segments-1; i++)); do
  # Clean up any unwanted characters from segment levels
  cleaned_level=$(echo "${segment_levels[i]}" | tr -d '[:space:]')
  cleaned_next_level=$(echo "${segment_levels[i+1]}" | tr -d '[:space:]')

  # If the levels are the same for consecutive segments
  if [ "$cleaned_level" = "$cleaned_next_level" ]; then
    # Calculate the combined size of the two segments
    combined_size=$((${segment_sizes[i]} + ${segment_sizes[i+1]}))

    # If the combined size is less than new_size, exit with failure
    if [ "$combined_size" -lt $((new_size * 1000000)) ]; then
      combined_size_mb=$(expr $combined_size / 1000000)
      segment_index=$((i+1))  # Compute index for next segment separately
      echo "Combined size of segment ${i} and segment ${segment_index} (LEVEL ${cleaned_level}) is less than ${new_size}MB."
      echo "Combined size: ${combined_size_mb}MB"
      echo "Test failed."
      exit 1
    fi
  fi
done

# If the loop completes without failure, the test passed
echo "All segment pairs with the same level have a combined size larger than ${new_size}MB. Test passed."

echo "Passed!"
shutdown
 