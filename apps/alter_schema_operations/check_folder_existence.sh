#!/usr/bin/env bash
set -eou pipefail

if [ "$#" -lt 4 ]; then
  echo "Usage: $0 [--location lsm|shard] <collection> <shard> <expect> <folder1> [folder2 ...]"
  echo "  --location: 'lsm' checks under <shard>/lsm/ (default), 'shard' checks under <shard>/"
  echo "  <expect>: 'exists' or 'absent'"
  echo "Example: $0 books HBVnMFMblTSi exists property_title property_title_searchable"
  echo "Example: $0 --location shard movies HBVnMFMblTSi absent vectors_hnsw_plain.hnsw.commitlog.d"
  exit 1
fi

LOCATION="lsm"
if [ "$1" = "--location" ]; then
  LOCATION="$2"
  shift 2
fi

if [ "$LOCATION" != "lsm" ] && [ "$LOCATION" != "shard" ]; then
  echo "Error: --location must be 'lsm' or 'shard', got '$LOCATION'"
  exit 1
fi

COLLECTION="$1"
SHARD="$2"
EXPECT="$3"
shift 3
FOLDERS=("$@")

if [ "$EXPECT" != "exists" ] && [ "$EXPECT" != "absent" ]; then
  echo "Error: <expect> must be 'exists' or 'absent', got '$EXPECT'"
  exit 1
fi

NAMESPACE="weaviate"
CONTAINER="weaviate"
BASE_PATH="/var/lib/weaviate/${COLLECTION}/${SHARD}"

PODS=("weaviate-0" "weaviate-1" "weaviate-2")

function echo_green() {
  green='\033[0;32m'; nc='\033[0m'; echo -e "${green}${*}${nc}"
}

function echo_red() {
  red='\033[0;31m'; nc='\033[0m'; echo -e "${red}${*}${nc}"
}

exit_code=0

for pod in "${PODS[@]}"; do
  echo "Checking pod: $pod (collection=${COLLECTION}, shard=${SHARD}, location=${LOCATION}, expect=${EXPECT})"
  for folder in "${FOLDERS[@]}"; do
    if [ "$LOCATION" = "lsm" ]; then
      path="${BASE_PATH}/lsm/${folder}"
    else
      path="${BASE_PATH}/${folder}"
    fi
    dir_exists=0
    kubectl -n "$NAMESPACE" exec -i "$pod" -c "$CONTAINER" -- test -d "$path" || dir_exists=1

    if [ "$EXPECT" = "exists" ]; then
      if [ "$dir_exists" -eq 0 ]; then
        echo_green "  [OK] $path exists"
      else
        echo_red "  [FAIL] $path does not exist (expected: exists)"
        exit_code=1
      fi
    else
      if [ "$dir_exists" -eq 1 ]; then
        echo_green "  [OK] $path does not exist"
      else
        echo_red "  [FAIL] $path exists (expected: absent)"
        exit_code=1
      fi
    fi
  done
done

if [ "$exit_code" -eq 0 ]; then
  echo_green "All checks passed (expect=${EXPECT})."
else
  echo_red "Some checks failed (expect=${EXPECT})."
fi

exit "$exit_code"
