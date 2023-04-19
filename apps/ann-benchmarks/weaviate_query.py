import grpc
import time
import uuid
import argparse
import weaviate
import h5py
import json
from loguru import logger

# from weaviategrpc import weaviate_pb2_grpc, weaviate_pb2

limit = 10
class_name = "Vector"
results = []


# def search_grpc_clientless(stub, dataset, i, input_vec):
#     out = {}
#     before = time.time()
#     req = weaviate_pb2.SearchRequest(
#         className=class_name,
#         limit=limit,
#         nearVector=weaviate_pb2.NearVectorParams(vector=input_vec),
#     )
#     res = stub.Search(req)
#     out["took"] = time.time() - before

#     ideal_neighbors = set(x for x in dataset["neighbors"][i][:limit])
#     res_ids = [uuid.UUID(res.id).int for res in res.results]

#     out["recall"] = len(ideal_neighbors.intersection(res_ids)) / limit
#     return out


def search_grpc(client: weaviate.Client, dataset, i, input_vec):
    out = {}
    before = time.time()
    res = (
        client.query.get(class_name, None)
        .with_additional("id")
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


def search_graphql(client: weaviate.Client, dataset, i, input_vec):
    out = {}
    before = time.time()
    res = (
        client.query.get(class_name, ["i _additional{id}"])
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


def query(client, stub, dataset, ef_values):
    schema = client.schema.get(class_name)
    shards = schema["shardingConfig"]["actualCount"]
    efC = schema["vectorIndexConfig"]["efConstruction"]
    m = schema["vectorIndexConfig"]["maxConnections"]
    logger.info(f"build params: shards={shards}, efC={efC}, m={m}")

    vectors = dataset["test"]

    for ef in ef_values:
        for api in ["grpc", "graphql"]:
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
            logger.info(
                f"mean={took}, qps={1/took}, recall={recall}, api={api}, ef={ef}, count={len(vectors)}"
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
                }
            )

    filename = f"./results/{int(time.time())}.json"
    logger.info(f"storing results in {filename}")
    with open(filename, "w") as f:
        f.write(json.dumps(results))
    logger.info("done storing results")
