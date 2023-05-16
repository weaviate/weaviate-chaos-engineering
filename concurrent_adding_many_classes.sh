#!/bin/bash

set -e


echo "Building all required containers"
( cd apps/concurrent_class_add/ && docker build -t concurrent_class_add . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml up -d

for i in {1..5}
do
   docker run -d --network host -t concurrent_class_add python3 concurrent_class_add.py
done

echo "Passed!"
