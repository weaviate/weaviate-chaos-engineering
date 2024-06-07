#!/bin/bash

set -e

git submodule update --init --remote --recursive

function logs() {
  pod_names=$(kubectl -n weaviate get pods -o jsonpath='{range.items[*]}{..metadata.name}{","}{end}')

  declare -a pod_names_array
  IFS=',' read -ra pod_names_array <<< "$pod_names"

  for pod_name in "${pod_names_array[@]}"; do
    if [[ $pod_name == *"weaviate"* ]]; then
      echo "show logs from $pod_name ------------------------------------------------"
      kubectl -n weaviate logs $pod_name
    fi
  done

  echo "kubectl -n weaviate get pods ------------------------------------------------"

  kubectl -n weaviate get pods

  echo "curl localhost:8080/v1/cluster/statistics response --------------------------"

  curl localhost:8080/v1/cluster/statistics | jq

  echo "kubectl -n weaviate describe svc weaviate-headless --------------------------"

  kubectl -n weaviate describe svc weaviate-headless
}
trap 'logs; exit 1' SIGINT ERR

echo "Building all required containers"
( cd apps/multi-tenancy-activate-deactivate/ && docker build -t multi-tenancy-activate-deactivate . )

echo "Run script"
docker run --network host --name multi-tenancy-activate-deactivate -t multi-tenancy-activate-deactivate

echo "Success"
