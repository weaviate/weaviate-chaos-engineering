# weaviate-chaos-engineering <img alt='Weaviate logo' src='https://raw.githubusercontent.com/semi-technologies/weaviate/19de0956c69b66c5552447e84d016f4fe29d12c9/docs/assets/weaviate-logo.png' width='180' align='right' />

> Chaos-Engineering-Style pipelines to make sure Weaviate behaves correctly,
> even when we try to sabotage it a bit.

## What this does

Currently there are two independent pipelines:

### Pipeline 1: Importing while Crashing

This pipelines is a Chaos-Monkey-style pipeline where we aim to import a large
dataset, but an external source constantly introduces crashes. The goal is to
make sure that every crash is recoverable and after the importer is through all
objects were indeed imported.

### Pipeline 2: Compare Recall after restarts

This pipelines imports a specific dataset (which will be generated as part of
the CI) and meassures the recall on it. Then Weaviate is stopped and restarted
with all state coming from disk and the recall is measured again. This pipeline
makes sure that all async operations we perform on commit logs (combining,
condensing, etc.) do not degrade the quality of the vector index.

## How to Run

### Requirements

- Bash
- Docker / Docker Compose
- `jq`

### Run the scripts

Everything is entirely containerized and all pipelines can be started with
simple bash scripts. You can find the scripts in the root folder, such as
`./import_while_crashing.sh` and `./compare_recall_after_restart.sh`. Or simply
check the Github actions YAML files for examples.

Here's an example of how to run the corrupted tenants test:

```sh
# TODO can i remove lsm strat?
WEAVIATE_VERSION=1.26.0-rc.0 DISABLE_RECOVERY_ON_PANIC=true PERSISTENCE_LSM_ACCESS_STRATEGY=mmap ./corrupted_tenants.sh
```

TODO organize
```sh
#t1
WEAVIATE_VERSION=1.26.0-rc.0 DISABLE_RECOVERY_ON_PANIC=true PERSISTENCE_LSM_ACCESS_STRATEGY=mmap ./corrupted_tenants.sh

#t5
docker run --rm --network host --name corrupted-tenants -t corrupted-tenants /app/corrupt_tens createschema localhost:8081
docker run --rm --network host --name corrupted-tenants -t corrupted-tenants /app/corrupt_tens createdata localhost:8081

#t2
ls apps/weaviate/data-node-1/pizza/
mv apps/weaviate/data-node-1/pizza/8saMYIiuGbkK/lsm/objects/segment-1719950203818308382.db apps/weaviate/data-node-1/pizza/8saMYIiuGbkK/lsm/objects/old.bak
touch apps/weaviate/data-node-1/pizza/8saMYIiuGbkK/lsm/objects/segment-1719950203818308382.db

#t3
docker compose -f apps/weaviate/docker-compose-replication.yml down
WEAVIATE_VERSION=1.26.0-rc.0 DISABLE_RECOVERY_ON_PANIC=true PERSISTENCE_LSM_ACCESS_STRATEGY=mmap docker compose -f apps/weaviate/docker-compose-replication.yml up

#t5
docker run --rm --network host --name corrupted-tenants -t corrupted-tenants /app/corrupt_tens getdataquorum localhost:8081
docker run --rm --network host --name corrupted-tenants -t corrupted-tenants /app/corrupt_tens getdataall localhost:8081


#t4
docker compose -f apps/weaviate/docker-compose-replication.yml down

docker run --rm --network host --name corrupted-tenants -t corrupted-tenants /app/corrupt_tens createschema

docker compose -f apps/weaviate/docker-compose-replication.yml down

WEAVIATE_VERSION=1.26.0-rc.0 DISABLE_RECOVERY_ON_PANIC=true PERSISTENCE_LSM_ACCESS_STRATEGY=mmap docker compose -f apps/weaviate/docker-compose-replication.yml up



# how to get shell into weaviate node running
docker compose -f apps/weaviate/docker-compose-replication.yml exec weaviate-node-1 sh

cd /var/lib/weaviate/pizza/*/lsm/objects
ls
mv segment-*.db segment-old.db.bak
touch segment-TODO.db

nate@Nathans-MacBook-Pro weaviate % mv data-node-1/pizza/fgLTfLjckepq/lsm/objects/segment-1719949209419682963.db data-node-1/pizza/fgLTfLjckepq/lsm/objects/old.bak
```


## Links 

- [Weaviate Main Repo](https://github.com/semi-technologies/weaviate).
- [Documentation](https://weaviate.io/developers/weaviate/current/client-libraries/javascript.html).
- [Stackoverflow for questions](https://stackoverflow.com/questions/tagged/weaviate).
- [Github for issues](https://github.com/semi-technologies/weaviate/issues).
