import grpc
import time
import uuid
import argparse
import weaviate
import h5py
import json
from loguru import logger

from weaviate_pprof import obtain_heap_profile

limit = 10
class_name = "Vector"
results = []


def search_grpc(client: weaviate.Client, dataset, i, input_vec):
    out = {}
    before = time.time()
    res = (
        client.query.get(class_name, None)
        .with_additional(weaviate.AdditionalProperties(uuid=True))
        .with_near_vector(
            {
                "vector": input_vec,
            }
        )
        .with_limit(limit)
        .do()
    )
    if "errors" in res and res["errors"] != None:
        logger.error(res["errors"])

    out["took"] = time.time() - before

    ideal_neighbors = set(x for x in dataset["neighbors"][i][:limit])
    res_ids = [uuid.UUID(res["_additional"]["id"]).int for res in res["data"]["Get"][class_name]]

    out["recall"] = len(ideal_neighbors.intersection(res_ids)) / limit
    return out


def query(client, stub, dataset, ef_values, labels):
    schema = client.schema.get(class_name)
    shards = schema["shardingConfig"]["actualCount"]
    efC = schema["vectorIndexConfig"]["efConstruction"]
    m = schema["vectorIndexConfig"]["maxConnections"]
    logger.info(f"build params: shards={shards}, efC={efC}, m={m} labels={labels}")

    vectors = dataset["test"]
    run_id = f"{int(time.time())}"

    for ef in ef_values:
        for api in ["grpc"]:
            schema = client.schema.get(class_name)
            schema["vectorIndexConfig"]["ef"] = ef
            client.schema.update_config(class_name, schema)

            took = 0
            recall = 0
            for i, vec in enumerate(vectors):
                res = {}
                if api == "grpc":
                    res = search_grpc(client, dataset, i, vec)
                elif api == "grpc_clientless":
                    res = search_grpc_clientless(stub, dataset, i, vec)
                elif api == "graphql":
                    res = search_graphql(client, dataset, i, vec)
                else:
                    logger.error(f"unknown api {api}")

                took += res["took"]
                recall += res["recall"]

            took = took / i
            recall = recall / i
            heap_mb = obtain_heap_profile("http://localhost:6060")
            logger.info(
                f"mean={took}, qps={1/took}, recall={recall}, api={api}, ef={ef}, count={len(vectors)}, heap_mb={heap_mb}"
            )

            results.append(
                {
                    "api": api,
                    "ef": ef,
                    "efConstruction": efC,
                    "maxConnections": m,
                    "mean": took,
                    "qps": 1 / took,
                    "recall": recall,
                    "shards": shards,
                    "heap_mb": heap_mb,
                    "run_id": run_id,
                    **labels,
                }
            )

    filename = f"./results/{run_id}.json"
    logger.info(f"storing results in {filename}")
    with open(filename, "w") as f:
        f.write(json.dumps(results))
    logger.info("done storing results")
