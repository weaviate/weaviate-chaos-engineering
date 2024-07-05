#!/usr/bin/env bash
set -eou pipefail

function echo_green() {
  green='\033[0;32m'; nc='\033[0m'; echo -e "${green}${*}${nc}"
}

pod_names=$(kubectl -n weaviate get pods -o jsonpath='{range.items[*]}{..metadata.name}{","}{end}')

declare -a pod_names_array
IFS=',' read -ra pod_names_array <<< "$pod_names"

for pod_name in "${pod_names_array[@]}"; do
  if [[ $pod_name == *"weaviate"* ]]; then
    echo_green "show logs from $pod_name ------------------------------------------------"
    kubectl -n weaviate logs $pod_name
  fi
done

echo_green "kubectl -n weaviate get pods ------------------------------------------------"

kubectl -n weaviate get pods

echo_green "curl localhost:8080/v1/cluster/statistics response --------------------------"

curl localhost:8080/v1/cluster/statistics | jq

echo_green "kubectl -n weaviate describe svc weaviate-headless --------------------------"

kubectl -n weaviate describe svc weaviate-headless
