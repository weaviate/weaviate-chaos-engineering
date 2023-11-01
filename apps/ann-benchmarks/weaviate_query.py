import grpc
import time
import uuid
import argparse
import weaviate
import h5py
import json
from loguru import logger

from weaviate_pprof import obtain_heap_profile
import weaviate.classes as wvc

limit = 10
class_name = "Vector"
results = []


def search_grpc(col: weaviate.collections.collection._Collection, dataset, i, input_vec):
    out = {}
    before = time.time()
    res = col.query.near_vector(
        near_vector=input_vec,
        limit=limit,
        return_metadata=wvc.MetadataQuery(uuid=True),
    )
    out["took"] = time.time() - before

    ideal_neighbors = set(x for x in dataset["neighbors"][i][:limit])
    res_ids = [obj.metadata.uuid.int for obj in res.objects]

    out["recall"] = len(ideal_neighbors.intersection(res_ids)) / limit

    return out


def query(client: weaviate.WeaviateClient, stub, dataset, ef_values, labels):
    col = client.collections.get(class_name)
    cfg = col.config.get()
    efC = cfg.vector_index_config.ef_construction
    m = cfg.vector_index_config.max_connections
    shards = cfg.sharding_config.actual_count
    logger.info(f"build params: shards={shards}, efC={efC}, m={m} labels={labels}")

    vectors = dataset["test"]
    run_id = f"{int(time.time())}"

    for ef in ef_values:
        for api in ["grpc"]:
            col.config.update(
                vector_index_config=wvc.Reconfigure.vector_index(
                    ef=ef,
                )
            )

            took = 0
            recall = 0
            for i, vec in enumerate(vectors):
                res = {}
                if api == "grpc":
                    res = search_grpc(col, dataset, i, vec)
                elif api == "grpc_clientless":
                    res = search_grpc_clientless(stub, dataset, i, vec)
                elif api == "graphql":
                    res = search_graphql(client, dataset, i, vec)
                else:
                    logger.error(f"unknown api {api}")

                took += res["took"]
                recall += res["recall"]

            took = took / len(vectors)
            recall = recall / len(vectors)
            heap_mb = -1
            try:
                heap_mb = obtain_heap_profile("http://localhost:6060")
            except:
                logger.error("could not obtain heap profile - ignoring")
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
