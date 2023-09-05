#!/bin/bash


function wait_weaviate() {
  echo "Wait for Weaviate to be ready on $1"
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:$1; then
      echo "Weaviate is ready"
      break
    fi

    echo "Weaviate is not ready on $1, trying again in 1s"
    sleep 1
  done
}

function clean_up() {
  echo "Removing S3 bucket..."
  docker compose -f apps/replicated_import_with_backup/docker-compose.yml up remove-s3-bucket

  echo "Shutting down Weaviate..."
  docker compose -f apps/replicated_import_with_backup/docker-compose.yml down --remove-orphans
}

function compose_exit_code() {
  echo $(docker inspect $1 --format='{{.State.ExitCode}}')
}


echo "Starting Weaviate..."
docker compose -f apps/replicated_import_with_backup/docker-compose.yml up -d \
  weaviate-node-1 \
  weaviate-node-2 \
  backup-s3
  
wait_weaviate 8080
wait_weaviate 8081

echo "Creating S3 bucket..."
docker compose -f apps/replicated_import_with_backup/docker-compose.yml up \
  create-s3-bucket

echo "Creating schema..."
docker compose -f apps/replicated_import_with_backup/docker-compose.yml up \
  importer-schema-node-1

if [ $(compose_exit_code importer-schema-node-1) -ne 0 ]; then
  echo "Could not apply schema"
  clean_up
  exit 1
fi

echo "Batch import to 2 nodes + parallel backup..."
docker compose -f apps/replicated_import_with_backup/docker-compose.yml up \
  importer-data-node-1 \
  importer-data-node-2 \
  backup-loop-node-1 \
  --abort-on-container-exit # stop remaining containers if any exited (due to finished work or error)

exit_code_imp_1=$(compose_exit_code importer-data-node-1)
exit_code_imp_2=$(compose_exit_code importer-data-node-2)
exit_code_bck=$(compose_exit_code backup-loop-node-1)
echo "node1 exit code: $exit_code_imp_1"
echo "node2 exit code: $exit_code_imp_2"
echo "loop exit code: $exit_code_bck"

# fail if any containers failed (exit code != 0 && exit code != 137)
# (137 = aborted by docker due to other container stopped))
if [[ $exit_code_imp_1 != 0 && $exit_code_imp_1 != 137 ]] || \
    [[ $exit_code_imp_2 != 0 && $exit_code_imp_2 != 137 ]] || \
    [[ $exit_code_bck != 0 && $exit_code_bck != 137 ]]; then
  echo "Could not import/backup"
  clean_up
  exit 1
fi  

clean_up
echo "Passed!"
