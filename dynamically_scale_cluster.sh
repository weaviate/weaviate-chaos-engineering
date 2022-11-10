function wait_weaviate_node() {
  port=$1
  echo "Wait for Weaviate on port $port to be ready"
  local ready=false
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$port; then
      echo "Weaviate node ($port) is ready"
      node1_ready=true
    fi

    if $node_ready; then
      return
    fi

    echo "Weaviate node on port $port is not ready, trying again in 1s"
    sleep 1
  done

  echo "Node never started up :-("
  exit 1
}

function dc() {
  docker-compose -f apps/weaviate/docker-compose-dynamic-cluster.yml $@
}

function main() {



echo "Building all required containers"
( cd apps/cluster-scaling/ && docker build -t scaling . )

  echo "Starting Node 1"
  dc up -d weaviate-1
  wait_weaviate_node 8080


  echo "import into first node"
  docker run --network host -it scaling python3 run.py -a schema -p 8080
  docker run --network host -it scaling python3 run.py -a import -p 8080

  echo "verify on first node"
  docker run --network host -it scaling python3 run.py -a verify -p 8080

  echo "Starting Node 2"
  dc up -d weaviate-2
  wait_weaviate_node 8081

  echo "verify on second node"
  docker run --network host -it scaling python3 run.py -a verify -p 8081

  echo "Stopping Node 2"
  dc stop weaviate-2
  echo "Restarting Node 2"
  dc up -d weaviate-2
  wait_weaviate_node 8081

  echo "verify on second node again after restart"
  docker run --network host -it scaling python3 run.py -a verify -p 8081
}

main "$@"
