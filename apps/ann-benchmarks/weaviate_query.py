import grpc
import time
import uuid
import argparse
import weaviate
import weaviate.classes.config as wvc
from weaviate.exceptions import WeaviateQueryException
import h5py
import torch
import json
from loguru import logger

from weaviate_pprof import obtain_heap_profile
from weaviate_import import wait_for_all_shards_ready

limit = 10
class_name = "Vector"
results = []


def search_grpc(
    collection: weaviate.collections.Collection, dataset, i, input_vec, multivector=False
):
    out = {}
    before = time.time()
    try:
        if not multivector:
            objs = collection.query.near_vector(
                near_vector=input_vec, limit=limit, return_properties=[]
            ).objects
        else:
            objs = collection.query.near_vector(
                near_vector=input_vec,
                limit=limit,
                target_vector="multivector",
                return_properties=[],
            ).objects
    except WeaviateQueryException as e:
        logger.error(e.message)
        objs = []

    out["took"] = time.time() - before

    ideal_neighbors = set(x for x in dataset["neighbors"][i][:limit])
    res_ids = [obj.uuid.int for obj in objs]

    out["recall"] = len(ideal_neighbors.intersection(res_ids)) / limit
    return out


def query(client: weaviate.WeaviateClient, stub, dataset, ef_values, labels, multivector=False, index_type="hnsw"):
    collection = client.collections.get(class_name)
    schema = collection.config.get()
    shards = schema.sharding_config.actual_count
    if index_type == "hnsw":
        if not multivector:
            efC = schema.vector_index_config.ef_construction
            m = schema.vector_index_config.max_connections
        else:
            efC = schema.vector_config["multivector"].vector_index_config.ef_construction
            m = schema.vector_config["multivector"].vector_index_config.max_connections
        logger.info(f"build params: shards={shards}, index_type={index_type}, efC={efC}, m={m} labels={labels}")
    elif index_type == "hfresh":
        efC = None
        ef_values = [16,128,512]
        logger.info(f"build params: shards={shards}, index_type={index_type} labels={labels}")
    else:
        logger.error(f"unknown index type {index_type}")
        return
    vectors = dataset["test"]
    if multivector:
        vector_dim: int = 128
        vectors = [torch.from_numpy(sample.reshape(-1, vector_dim)) for sample in vectors]
    run_id = f"{int(time.time())}"

    for ef in ef_values:
        for api in ["grpc"]:
            if not multivector:
                if index_type == "hnsw":
                    collection.config.update(
                            vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(ef=ef)
                        )
                elif index_type == "hfresh":
                    collection.config.update(
                        vector_index_config=wvc.Reconfigure.VectorIndex.hfresh(search_probe=ef)
                    )
                else:
                    logger.error(f"unknown index type {index_type}")
                    return
            else:
                collection.config.update(
                    vectorizer_config=[
                        wvc.Reconfigure.NamedVectors.update(
                            name="multivector",
                            vector_index_config=wvc.Reconfigure.VectorIndex.hnsw(ef=ef),
                        )
                    ]
                )

            wait_for_all_shards_ready(client)

            took = 0
            recall = 0
            for i, vec in enumerate(vectors):
                res = {}
                if api == "grpc":
                    res = search_grpc(collection, dataset, i, vec.tolist(), multivector)
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
                    "index_type": index_type,
                    "efConstruction": efC if index_type == "hnsw" else None,
                    "maxConnections": m if index_type == "hnsw" else None,
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
