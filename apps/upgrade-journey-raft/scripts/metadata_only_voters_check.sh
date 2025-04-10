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

function get_node_names() {
  echo $(curl -sf $AUTH_BEARER localhost:${WEAVIATE_PORT}/v1/nodes | jq -r '.nodes[].name' | tr '\n' ',')
}

function get_setting() {
  kubectl -n weaviate get pod $1 -o=jsonpath='{.spec.containers[].env[*]}{.name}'|jq "select(.name==\"$2\").value"
}

function get_additional_objects_files_count() {
  kubectl -n weaviate exec -i $node_name -c weaviate -- find /var/lib/weaviate/ -type f | grep -c mtclassadditional || echo ""
}

function check_node() {
  node_name=$1
  metadata_only_voters=$(get_setting $node_name RAFT_METADATA_ONLY_VOTERS)
  voter_nodes=$(get_setting $node_name RAFT_JOIN)

  if [[ "$metadata_only_voters" == "\"true\"" ]]; then
    is_voter_node="false"
    if [[ $voter_nodes == *$node_name* ]]; then
      is_voter_node="true"
    fi

    additional_class_files_count=$(get_additional_objects_files_count)
    if [[ "$additional_class_files_count" == "0" && "$is_voter_node" == "true" ]]; then
      echo "true"
    elif [[ "$additional_class_files_count" != "0" && "$is_voter_node" == "false" ]]; then
      echo "true"
    else
      echo "false"
    fi
  else
    echo "false"
  fi
}

function check_additional_objects_existence() {
  node_names=$(get_node_names)
  echo_green "check additional class objects DB files existence on all nodes in cluster"
  IFS=',' read -ra node_names_array <<< "$node_names"
  for node_name in "${node_names_array[@]}"; do
    ok=$(check_node $node_name)
    if [[ "$ok" == "false" ]]; then
      echo_red "check for $node_name failed"
      exit 1
    else
      echo_yellow "checking node: $node_name, result: OK"
    fi
  done
  echo_green "success"
}

check_additional_objects_existence
