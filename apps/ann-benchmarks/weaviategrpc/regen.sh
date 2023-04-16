#!/bin/bash

cd "${0%/*}/.."

echo "this script assumes that you have checked out weaviate next to weaviate-chaos-engineering"

python3 -m grpc_tools.protoc  -I ../../../weaviate/grpc --python_out=. --pyi_out=weaviategrpc --grpc_python_out=weaviategrpc ../../../weaviate/grpc/weaviate.proto


echo "done"
