#!/bin/bash
# Offload S3: cloud provider down during freeze
#
# Chaos scenario: request tenant FROZEN (offload to S3), terminate MinIO while offload
# is in progress, then assert tenant is reset to HOT (abort path).

set -e

source common.sh

export COMPOSE="apps/weaviate/docker-compose-offload-s3.yml"
WEAVIATE_URL="${WEAVIATE_URL:-http://localhost:8080}"
CLASS_NAME="MultiTenantClass"
TENANT_NAME="Tenant1"
POLL_TIMEOUT=60
POLL_INTERVAL=2


echo "Starting Weaviate (3-node) + MinIO with offload-s3 (OFFLOAD_TIMEOUT=$OFFLOAD_TIMEOUT, OFFLOAD_S3_CONCURRENCY=$OFFLOAD_S3_CONCURRENCY, OFFLOAD_S3_WORKERS=$OFFLOAD_S3_WORKERS)..."
docker compose -f "$COMPOSE" up -d weaviate-node-1 weaviate-node-2 weaviate-node-3 test-minio

echo "Waiting for Weaviate to be ready on 8080..."
wait_weaviate 8080 120

echo "Creating class with multi-tenancy..."
curl -s -X POST "$WEAVIATE_URL/v1/schema" -H "Content-Type: application/json" -d '{
  "class": "'"$CLASS_NAME"'",
  "replicationConfig": { "factor": 3 },
  "multiTenancyConfig": { "enabled": true },
  "properties": [{ "name": "name", "dataType": ["text"] }]
}'
echo ""

echo "Creating tenant..."
curl -s -X POST "$WEAVIATE_URL/v1/schema/$CLASS_NAME/tenants" -H "Content-Type: application/json" -d '[
  { "name": "'"$TENANT_NAME"'" }
]'
echo ""


TOTAL_OBJECTS="${TOTAL_OBJECTS:-500000}"
BATCH_SIZE="${BATCH_SIZE:-1000}"
echo "Starting offload-importer (TOTAL_OBJECTS=$TOTAL_OBJECTS, BATCH_SIZE=$BATCH_SIZE)..."
TOTAL_OBJECTS="$TOTAL_OBJECTS" BATCH_SIZE="$BATCH_SIZE" docker compose -f "$COMPOSE" up --build --abort-on-container-exit offload-importer
echo "Importer finished."

echo "Setting tenant to FROZEN (starts offload)..."
curl -s -X PUT "$WEAVIATE_URL/v1/schema/$CLASS_NAME/tenants" -H "Content-Type: application/json" -d '[{"name":"'"$TENANT_NAME"'","activityStatus":"FROZEN"}]'
echo ""


echo "Stopping MinIO (chaos: provider down)..."
docker compose -f "$COMPOSE" stop test-minio

echo "Polling for tenant status HOT (timeout ${POLL_TIMEOUT}s)..."
elapsed=0
while [[ $elapsed -lt $POLL_TIMEOUT ]]; do
  status=$(curl -s "$WEAVIATE_URL/v1/schema/$CLASS_NAME/tenants" | jq -r '.[] | select(.name=="'"$TENANT_NAME"'") | .activityStatus')
  if [[ "$status" == "HOT" ]]; then
    echo "Tenant is HOT (reverted as expected). Success."
    shutdown
    exit 0
  fi
  echo "  status=$status (elapsed ${elapsed}s)"
  sleep $POLL_INTERVAL
  elapsed=$((elapsed + POLL_INTERVAL))
done

echo "Timeout: tenant did not become HOT (last status: $status)"
shutdown
exit 1
