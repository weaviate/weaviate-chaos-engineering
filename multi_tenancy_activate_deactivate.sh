#!/bin/bash

set -e

git submodule update --init --remote --recursive

echo "Building all required containers"
( cd apps/multi-tenancy-activate-deactivate/ && docker build -t multi-tenancy-activate-deactivate . )

echo "Run script"
docker run --network host --name multi-tenancy-activate-deactivate -t multi-tenancy-activate-deactivate

echo "Success"
