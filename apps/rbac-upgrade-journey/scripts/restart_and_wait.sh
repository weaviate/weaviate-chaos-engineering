#!/usr/bin/env bash
set -eou pipefail

WEAVIATE_PORT=${WEAVIATE_PORT:-8080}
WEAVIATE_API_KEY=${WEAVIATE_API_KEY:-""}

AUTH_BEARER=""
if [[ -n "$WEAVIATE_API_KEY" ]]; then
  AUTH_BEARER="-H \"Authorization: Bearer $WEAVIATE_API_KEY\""
fi

function echo_green() {
  green='\033[0;32m'; nc='\033[0m'; echo -e "${green}${*}${nc}"
}

function echo_yellow() {
  yellow='\033[0;33m'; nc='\033[0m'; echo -e "${yellow}${*}${nc}"
}

function echo_red() {
  red='\033[0;31m'; nc='\033[0m'; echo -e "${red}${*}${nc}"
}

function curl_with_auth() {
  local url="$1"
  local extra_args="${2:-}"
  eval "curl -sf $AUTH_BEARER \"$url\" $extra_args"
}

function wait_weaviate() {

  echo_green "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl_with_auth "localhost:${WEAVIATE_PORT}" "-o /dev/null"; then
      echo_green "Weaviate is ready"
      return
    fi

    echo_yellow "Weaviate is not ready, trying again in 1s"
    sleep 1
  done
  echo_red "Weaviate is not ready"
  exit 1
}

function is_node_healthy() {
  node=$1
  response=$(curl_with_auth "localhost:${WEAVIATE_PORT}/v1/nodes")
  if echo "$response" | jq ".nodes[] | select(.name == \"${node}\" ) | select (.status == \"HEALTHY\" )" | grep -q "$node"; then
    echo "true"
  else
    echo "false"
  fi
}

function wait_for_all_healthy_nodes() {
  replicas=$1
  echo_green "Wait for all Weaviate $replicas nodes in cluster"
  for _ in {1..120}; do
    healthy_nodes=0
    for i in $(seq 0 $((replicas-1))); do
      node="weaviate-$i"
      if [ "$(is_node_healthy "$node")" == "true" ]; then
        healthy_nodes=$((healthy_nodes+1))
      else
        echo_yellow "Weaviate node $node is not healthy"
      fi
    done

    if [ "$healthy_nodes" == "$replicas" ]; then
      echo_green "All Weaviate $replicas nodes in cluster are healthy"
      return
    fi

    echo_yellow "Not all Weaviate nodes in cluster are healthy, trying again in 2s"
    sleep 2
  done
  echo_red "Weaviate $replicas nodes in cluster are not healthy"
  exit 1
}

# Perform rolling restart
echo_green "Performing rolling restart of Weaviate"
kubectl rollout restart statefulset weaviate -n weaviate

# Wait for pods to be up and running
echo_green "Waiting for pods to be up and running"
kubectl rollout status statefulset weaviate -n weaviate

# Wait for Weaviate to be ready
wait_weaviate

# Get the number of replicas
replicas=$(kubectl get statefulset weaviate -n weaviate -o=jsonpath='{.spec.replicas}')

# Wait for all nodes to be healthy
wait_for_all_healthy_nodes $replicas

echo_green "Rolling restart completed successfully"
