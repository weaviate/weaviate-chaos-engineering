#!/usr/bin/env bash
set -eou pipefail

WEAVIATE_API_KEY=${WEAVIATE_API_KEY:-""}
# Define the absolute path for log storage
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_DIR="${SCRIPT_DIR}/../logs"

AUTH_BEARER=""
if [[ -n "$WEAVIATE_API_KEY" ]]; then
  AUTH_BEARER="-H \"Authorization: Bearer $WEAVIATE_API_KEY\""
fi

function echo_green() {
  green='\033[0;32m'; nc='\033[0m'; echo -e "${green}${*}${nc}"
}

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"
echo_green "Storing logs in: $LOG_DIR"

pod_names=$(kubectl -n weaviate get pods -o jsonpath='{range.items[*]}{..metadata.name}{","}{end}')

declare -a pod_names_array
IFS=',' read -ra pod_names_array <<< "$pod_names"

for pod_name in "${pod_names_array[@]}"; do
  if [[ $pod_name == *"weaviate"* ]]; then
    echo_green "store logs from $pod_name ------------------------------------------------"
    kubectl -n weaviate logs $pod_name > "$LOG_DIR/${pod_name}_logs.txt"
  fi
done

echo_green "store kubectl -n weaviate get pods output ------------------------------------------------"
kubectl -n weaviate get pods > "$LOG_DIR/pods_status.txt"

echo_green "store curl $AUTH_BEARER localhost:8080/v1/cluster/statistics response --------------------------"
curl $AUTH_BEARER localhost:8080/v1/cluster/statistics | jq > "$LOG_DIR/cluster_statistics.json"

echo_green "store kubectl -n weaviate describe svc weaviate-headless output --------------------------"
kubectl -n weaviate describe svc weaviate-headless > "$LOG_DIR/weaviate_headless_service.txt"

# Create a summary file with all the log locations
cat > "$LOG_DIR/log_summary.txt" << EOF
Log files generated at: $LOG_DIR
- Pod logs: ${LOG_DIR}/*_logs.txt
- Pod status: ${LOG_DIR}/pods_status.txt
- Cluster statistics: ${LOG_DIR}/cluster_statistics.json
- Headless service description: ${LOG_DIR}/weaviate_headless_service.txt
EOF

echo_green "All logs have been stored in: $LOG_DIR"
