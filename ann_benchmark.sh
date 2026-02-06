#!/bin/bash

set -euo pipefail

# Configuration
readonly DATASET="${DATASET:-sift-128-euclidean}"
readonly DISTANCE="${DISTANCE:-l2-squared}"
readonly QUANTIZATION="${QUANTIZATION:-none}"
readonly MULTIVECTOR_DATASET="${MULTIVECTOR_DATASET:-false}"
readonly MULTIVECTOR_IMPLEMENTATION="${MULTIVECTOR_IMPLEMENTATION:-regular}"
readonly WEAVIATE_VERSION="${WEAVIATE_VERSION:-}"
readonly CLOUD_PROVIDER="${CLOUD_PROVIDER:-}"
readonly MACHINE_TYPE="${MACHINE_TYPE:-}"
readonly OS="${OS:-}"
readonly REQUIRED_RECALL="${REQUIRED_RECALL:-}"
readonly INDEX_TYPE="${INDEX_TYPE:-hnsw}"
# Constants
readonly WEAVIATE_COMPOSE_FILE="apps/weaviate-no-restart-on-crash/docker-compose.yml"
readonly ANNBENCHMARKS_IMAGE="ann_benchmarks"
readonly WEAVIATE_CONTAINER="weaviate-no-restart-on-crash-weaviate-1"
readonly WEAVIATE_READY_TIMEOUT=120
readonly CACHE_WARMUP_DELAY=30
readonly RESTART_DELAY=10
readonly CHAOS_ITERATIONS=5

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Error handling
handle_error() {
    local exit_code=$?
    log_error "Script failed with exit code $exit_code"
    exit $exit_code
}

trap handle_error ERR

# Wait for Weaviate to be ready
wait_for_weaviate() {
    log_info "Waiting for Weaviate to be ready..."
    local attempts=0
    while [ $attempts -lt $WEAVIATE_READY_TIMEOUT ]; do
        if curl -sf -o /dev/null localhost:8080/v1/.well-known/ready; then
            log_success "Weaviate is ready"
            return 0
        fi
        
        attempts=$((attempts + 1))
        log_info "Weaviate is not ready, attempt $attempts/$WEAVIATE_READY_TIMEOUT"
        sleep 1
    done
    
    log_error "Weaviate is not ready after ${WEAVIATE_READY_TIMEOUT}s"
    exit 1
}

# Build required containers
build_containers() {
    log_info "Building all required containers..."
    (cd apps/ann-benchmarks/ && docker build -t "$ANNBENCHMARKS_IMAGE" .)
}

# Start Weaviate
start_weaviate() {
    log_info "Starting Weaviate..."
    docker compose -f "$WEAVIATE_COMPOSE_FILE" up -d
    wait_for_weaviate
}

# Stop Weaviate
stop_weaviate() {
    log_info "Stopping Weaviate..."
    docker compose -f "$WEAVIATE_COMPOSE_FILE" stop weaviate
}

# Restart Weaviate
restart_weaviate() {
    log_info "Restarting Weaviate..."
    docker compose -f "$WEAVIATE_COMPOSE_FILE" start weaviate
    wait_for_weaviate
}

# Download dataset if not exists
download_dataset() {
    log_info "Checking dataset availability..."
    mkdir -p datasets
    
    if [ -f "datasets/${DATASET}.hdf5" ]; then
        log_info "Dataset exists locally"
        return 0
    fi
    
    log_info "Downloading dataset..."
    cd datasets
    
    if [ "$MULTIVECTOR_DATASET" = "true" ]; then
        log_info "Downloading multivector dataset"
        curl -LO "https://storage.googleapis.com/ann-datasets/custom/Multivector/${DATASET}.hdf5"
    else
        log_info "Downloading single vector dataset"
        curl -LO "http://ann-benchmarks.com/${DATASET}.hdf5"
    fi
    
    cd ..
}

# Get multivector flag
get_multivector_flag() {
    if [ "$MULTIVECTOR_DATASET" = "true" ]; then
        echo "-mv"
        if [ "$MULTIVECTOR_IMPLEMENTATION" = "muvera" ]; then
            echo "-mi muvera"
        fi
    else
        echo ""
    fi
}

# Build benchmark labels
build_benchmark_labels() {
    local after_restart="$1"
    echo "after_restart=$after_restart,weaviate_version=$WEAVIATE_VERSION,cloud_provider=$CLOUD_PROVIDER,machine_type=$MACHINE_TYPE,os=$OS,index_type=$INDEX_TYPE"
}

# Run benchmark
run_benchmark() {
    local multivector_flag="$1"
    local labels="$2"
    local additional_args="${3:-}"
 
    log_info "Running benchmark with labels: $labels"

    local max_connections
    local dim_to_segment_arg=""

    if [ "$QUANTIZATION" != "none" ]; then
        max_connections=16
        dim_to_segment_arg="--dim-to-segment-ratio 4"
    else
        max_connections=32
    fi
    
    docker run --network host -t \
        -v "$PWD/datasets:/datasets" \
        -v "$PWD/results:/workdir/results" \
        "$ANNBENCHMARKS_IMAGE" \
        python3 run.py $multivector_flag \
        -v "/datasets/${DATASET}.hdf5" \
        -d "$DISTANCE" \
        -m $max_connections \
        $dim_to_segment_arg \
        --quantization "$QUANTIZATION" \
        --index-type "$INDEX_TYPE" \
        --labels "$labels" \
        $additional_args
}

# Check Weaviate logs for specific patterns
check_weaviate_logs() {
    log_info "Checking Weaviate logs..."
    
    echo "Checking if prefilled cache is used:"
    docker logs "$WEAVIATE_CONTAINER" 2>&1 | grep "prefilled" || log_warning "No prefilled cache entries found"
    
    echo "Checking if completed loading shard:"
    docker logs "$WEAVIATE_CONTAINER" 2>&1 | grep -i "completed loading shard" || log_warning "No shard loading completion found"
    
    echo "Checking for errors in logs:"
    docker logs "$WEAVIATE_CONTAINER" 2>&1 | grep -i "erro" || log_info "No errors found in logs"
    
    if [ "${1:-}" = "after_chaos" ]; then
        echo "Checking for panics in logs:"
        docker logs "$WEAVIATE_CONTAINER" 2>&1 | grep -i "panic" || log_info "No panics found in logs"
    fi
}

# Run analysis
run_analysis() {
    log_info "Running analysis..."
    docker run --network host -t \
        -v "$PWD/datasets:/datasets" \
        -v "$PWD/results:/workdir/results" \
        -e "REQUIRED_RECALL=$REQUIRED_RECALL" \
        "$ANNBENCHMARKS_IMAGE" \
        python3 analyze.py
}

# Run chaos testing
run_chaos_testing() {
    log_info "Starting chaos testing with $CHAOS_ITERATIONS iterations..."
    
    # Set environment variable for HNSW max log size
    export PERSISTENCE_HNSW_MAX_LOG_SIZE=100MB
    log_info "Set PERSISTENCE_HNSW_MAX_LOG_SIZE to 100MB"
    
    for i in $(seq 1 $CHAOS_ITERATIONS); do
        log_info "Chaos iteration $i/$CHAOS_ITERATIONS"
        
        # Start Weaviate
        docker compose -f "$WEAVIATE_COMPOSE_FILE" up -d
        
        log_info "Waiting ${RESTART_DELAY}s for Weaviate to restart"
        sleep $RESTART_DELAY
        
        log_info "Killing Weaviate (PKILL)"
        docker exec -it "$WEAVIATE_CONTAINER" pkill -9 -f /bin/weaviate || true
    done
    
    # Final restart
    log_info "Final restart of Weaviate..."
    docker compose -f "$WEAVIATE_COMPOSE_FILE" start weaviate
    wait_for_weaviate
}

# Main execution
main() {
    log_info "Starting ANN benchmark"
    log_info "Dataset: $DATASET, Distance: $DISTANCE, Multivector: $MULTIVECTOR_DATASET"

    # Download dataset
    download_dataset

    # Build containers
    build_containers
    
    # Start Weaviate and run initial benchmark
    start_weaviate
    
    local multivector_flag
    multivector_flag=$(get_multivector_flag)
    
    local initial_labels
    initial_labels=$(build_benchmark_labels "false")
    
    run_benchmark "$multivector_flag" "$initial_labels"
    
    # Restart and run query-only benchmark
    log_info "Initial run complete, now restart Weaviate"
    stop_weaviate
    restart_weaviate
    
    log_info "Weaviate ready, waiting ${CACHE_WARMUP_DELAY}s for caches to be hot"
    sleep $CACHE_WARMUP_DELAY
    
    log_info "Running second benchmark (query only)"
    sleep 30  # Additional delay to reduce flakiness
    
    local restart_labels
    restart_labels=$(build_benchmark_labels "true")
    
    run_benchmark "$multivector_flag" "$restart_labels" "--query-only"
    
    # Check logs and run analysis
    check_weaviate_logs
    run_analysis
    
    # Stop Weaviate for chaos testing
    stop_weaviate
    
    # Run chaos testing
    run_chaos_testing

    # Delete last result file. Use sudo to remove files in Linux machines, required for docker mounted volumes
    sudo rm -f "$(ls -t results/*.json | head -1)"
    
    # Final benchmark after chaos
    log_info "Running final benchmark after chaos testing"
    run_benchmark "$multivector_flag" "$restart_labels" "--query-only"
    
    # Final log checks and analysis
    check_weaviate_logs "after_chaos"
    run_analysis
    
    # Cleanup
    stop_weaviate
    
    log_success "All benchmarks completed successfully!"
}

# Execute main function
main "$@"
