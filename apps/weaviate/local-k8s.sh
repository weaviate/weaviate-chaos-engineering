#!/bin/bash
set -eou pipefail

REQUIREMENTS=(
  "kind"
  "helm"
  "kubectl"
  "curl"
  "nohup"
)

# Due to the nature of the ingress, you can't change the Weaviate port. If for whatever reason you would like to use
# a different port you can set it in this WEAVIATE_PORT variable, but you will have to comment all ingress logic and uncomment
# the kubectl port-forward line.
WEAVIATE_PORT=8080
WEAVIATE_GRPC_PORT=50051
PROMETHEUS_PORT=9091
GRAFANA_PORT=3000

function wait_weaviate() {
  echo "Wait for Weaviate to be ready"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:${WEAVIATE_PORT}; then
      echo "Weaviate is ready"
      break
    fi

    echo "Weaviate is not ready, trying again in 1s"
    sleep 1
  done
}

# This auxiliary function returns the number of voters based on the number of nodes, passing the number of nodes as an argument.
function get_voters() {
    if [[ $1 -ge 10 ]]; then
        echo 7
    elif [[ $1 -ge 7 && $1 -lt 10 ]]; then
        echo 5
    elif [[ $1 -ge 4 && $1 -lt 7 ]]; then
        echo 3
    elif [[ $1 -ge 3 && $1 -lt 4 ]]; then
        echo 2
    else
        echo 1
    fi
}

function setup() {

    echo "setup # Setting up Weaviate on local k8s"

    # Create Kind config file
    cat <<EOF > /tmp/kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: weaviate-k8s
nodes:
- role: control-plane
$(for i in $(seq 1 $REPLICAS); do echo "- role: worker"; done)
EOF

    echo "setup # Create local k8s cluster"
    # Create k8s Kind Cluster
    kind create cluster --wait 120s --name weaviate-k8s --config /tmp/kind-config.yaml

    # Create namespace
    kubectl create namespace weaviate

    if [ -n "${HELM_BRANCH:-}" ]; then
        WEAVIATE_HELM_DIR="/tmp/weaviate-helm"
        # Delete $WEAVIATE_HELM_DIR if it already exists
        if [ -d "$WEAVIATE_HELM_DIR" ]; then
            rm -rf "$WEAVIATE_HELM_DIR"
        fi
        # Download weaviate-helm repository master branch
        git clone -b $HELM_BRANCH https://github.com/weaviate/weaviate-helm.git $WEAVIATE_HELM_DIR
        # Package Weaviate Helm chart
        helm package -d ${WEAVIATE_HELM_DIR} ${WEAVIATE_HELM_DIR}/weaviate
        TARGET=${WEAVIATE_HELM_DIR}/weaviate-*.tgz
    else
        helm repo add weaviate https://weaviate.github.io/weaviate-helm
        TARGET="weaviate/weaviate"
    fi

    # Install Weaviate using Helm
    helm upgrade --install weaviate $TARGET \
    --namespace weaviate \
    --set image.tag=$WEAVIATE_VERSION \
    --set replicas=$REPLICAS \
    --set grpcService.enabled=true \
    --set env.RAFT_BOOTRSTRAP_EXPECT=$(get_voters $REPLICAS) \
    --set env.LOG_LEVEL="debug"
    #--set debug=true

    # Wait for Weaviate to be up
    kubectl wait sts/weaviate -n weaviate --for jsonpath='{.status.readyReplicas}'=${REPLICAS} --timeout=100s

    # Install kube-relay tool to perform port-forwarding
    # Check if kubectl-relay binary is available
    if ! command -v kubectl-relay &> /dev/null; then
        # Retrieve the operating system
        OS=$(uname -s)

        # Retrieve the processor architecture
        ARCH=$(uname -m)

        VERSION="v0.0.6"

        # Determine the download URL based on the OS and ARCH
        if [[ $OS == "Darwin" && $ARCH == "x86_64" ]]; then
            OS_ID="darwin"
            ARCH_ID="amd64"
        elif [[ $OS == "Darwin" && $ARCH == "arm64" ]]; then
            OS_ID="darwin"
            ARCH_ID="arm64"
        elif [[ $OS == "Linux" && $ARCH == "x86_64" ]]; then
            OS_ID="linux"
            ARCH_ID="amd64"
        elif [[ $OS == "Linux" && $ARCH == "aarch64" ]]; then
            OS_ID="linux"
            ARCH_ID="arm64"
        else
            echo "Unsupported operating system or architecture"
            exit 1
        fi

        KUBE_RELAY_FILENAME="kubectl-relay_${VERSION}_${OS_ID}-${ARCH_ID}.tar.gz"
        # Download the appropriate version
        curl -L "https://github.com/knight42/krelay/releases/download/${VERSION}/${KUBE_RELAY_FILENAME}" -o /tmp/${KUBE_RELAY_FILENAME}
        
        # Extract the downloaded file
        tar -xzf "/tmp/${KUBE_RELAY_FILENAME}" -C /tmp
        
        # Copy the binary to the desired location
        #cp /tmp/kubectl-relay "$(go env GOPATH)/bin/kubectl-relay"
    fi

    /tmp/kubectl-relay svc/weaviate -n weaviate ${WEAVIATE_PORT}:80 -n weaviate &> /tmp/weaviate_frwd.log &

    /tmp/kubectl-relay svc/weaviate-grpc -n weaviate ${WEAVIATE_GRPC_PORT}:50051 -n weaviate &> /tmp/weaviate_grpc_frwd.log &
    # Install nginx-ingress controller and ingress to make the weaviate loadbalancer service available $WEAVIATE_PORT
    # Alternative, port-forwarding, but the connection is loss during rolling updates:
    # nohup kubectl port-forward --pod-running-timeout=0 svc/weaviate ${WEAVIATE_PORT}:80 -n weaviate &

#     kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
#     kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=90s

#     # Define an ingress that allows redirecting all traffic to localhost into weaviate LB service.
#     kubectl apply -f - <<EOF
# apiVersion: networking.k8s.io/v1
# kind: Ingress
# metadata:
#   name: weaviate-ingress
#   namespace: weaviate
#   annotations:
#     nginx.ingress.kubernetes.io/rewrite-target: /\$2
# spec:
#   ingressClassName: nginx
#   defaultBackend:
#     service:
#       name: weaviate
#       port:
#         number: 80
# EOF



    # Expose gRPC port locally into 50051
#    nohup kubectl port-forward svc/weaviate-grpc ${WEAVIATE_GRPC_PORT}:50051 -n weaviate &

    wait_weaviate

    # Check if Weaviate is up
    curl http://localhost:${WEAVIATE_PORT}/v1/nodes
}

function clean() {
    echo "clean # Cleaning up local k8s cluster..."

    # Kill kubectl port-forward processes running in the background
    pkill -f "kubectl-relay" || true

    # Check if Weaviate release exists
    if helm status weaviate -n weaviate &> /dev/null; then
        # Uninstall Weaviate using Helm
        helm uninstall weaviate -n weaviate
    fi

    # Check if Weaviate namespace exists
    if kubectl get namespace weaviate &> /dev/null; then
        # Delete Weaviate namespace
        kubectl delete namespace weaviate
    fi

    # Check if Kind cluster exists
    if kind get clusters | grep -q "weaviate-k8s"; then
        # Delete Kind cluster
        kind delete cluster --name weaviate-k8s
    fi
}


# Main script

# Check if any options are passed
if [ $# -eq 0 ]; then
    echo "Usage: $0 <options>"
    echo "options:"
    echo "         setup"
    echo "         clean"
    exit 1
fi

# Check if all requirements are installed
for requirement in "${REQUIREMENTS[@]}"; do
  if ! command -v $requirement &> /dev/null; then
    echo "Please install '$requirement' before running this script"
    echo "    brew install $requirement"
    exit 1
  fi
done

# Process command line options
case $1 in
    "setup")
        setup
        ;;
    "clean")
        clean
        ;;
    *)
        echo "Invalid option: $1. Use 'setup' or 'clean'"
        exit 1
        ;;
esac

# Retrieve Weaviate logs
if [ $? -ne 0 ]; then
  kubectl logs -n weaviate -l app.kubernetes.io/name=weaviate
fi
