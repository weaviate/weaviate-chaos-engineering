#!/bin/bash

set -e


echo "Building all required containers"
( cd apps/concurrent_class_add/ && docker build -t concurrent_class_add . )

echo "Starting Weaviate..."
docker-compose -f apps/weaviate/docker-compose.yml up -d

for i in {1..400}
do
   docker run -d --network host -t concurrent_class_add python3 concurrent_class_add.py "$i"
done

# have one script we wait for
docker run --network host -t concurrent_class_add python3 concurrent_class_add.py 500

echo "Passed!"
