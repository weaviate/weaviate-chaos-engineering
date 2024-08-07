#!/bin/bash

set -e

source common.sh

git submodule update --init --remote --recursive


echo "Starting Weaviate..."
docker compose -f apps/weaviate/docker-compose-replication.yml up -d weaviate-node-1 weaviate-node-2 weaviate-node-3
wait_weaviate 8080
wait_weaviate 8081
wait_weaviate 8082

echo "Run importer scripts in the background"
docker compose -f apps/multi-tenancy-concurrent-imports/docker-compose-importers.yml up schema-resetter
docker compose -f apps/multi-tenancy-concurrent-imports/docker-compose-importers.yml up -d importer-01 importer-02 importer-03 importer-04

echo "Run checker script in the foreground"
docker compose -f apps/multi-tenancy-concurrent-imports/docker-compose-importers.yml up corruption-checker --exit-code-from corruption-checker

echo "Success"
shutdown
