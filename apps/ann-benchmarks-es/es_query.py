import time
import uuid
import argparse
from elasticsearch import ConnectionError, Elasticsearch
from elasticsearch.helpers import bulk
import h5py
import json
from loguru import logger

limit = 10
results = []
num_candidates = 100

def search_es(client, index_name, dataset, i, vec, k):
    if k > num_candidates:
        raise ValueError("n must be smaller than num_candidates")

    body = {
        "knn": {
            "field": "vec",
            "query_vector": vec,
            "k": k,
            "num_candidates": num_candidates,
        }
    }

    before = time.time()
    out = {}

    res = client.search(
        index=index_name,
        body=body,
        size=k,
        _source=False,
        docvalue_fields=["id"],
        stored_fields="_none_",
        filter_path=["hits.hits.fields.id"],
        request_timeout=10,
    )
    out["took"] = time.time() - before

    res_ids = [int(h["fields"]["id"][0]) for h in res["hits"]["hits"]]

    ideal_neighbors = set(x for x in dataset["neighbors"][i][:limit])

    out["recall"] = len(ideal_neighbors.intersection(res_ids)) / limit
    return out

def query(client, index_name, dataset, labels):
    _wait_for_health_status(client)

    shards = client.indices.get_settings(index=index_name)[index_name]['settings']['index']['number_of_shards']
    mapping = client.indices.get_mapping(index=index_name)[index_name]
    props = mapping['mappings']['properties']['vec']
    efC = props['index_options']['ef_construction']
    m = props['index_options']['m']
    logger.info(f"build params: shards={shards}, efC={efC}, m={m} labels={labels}")

    vectors = dataset["test"]
    run_id = f"{int(time.time())}"

    took = 0
    recall = 0
    for i, vec in enumerate(vectors):
        res = {}
        res = search_es(client, index_name, dataset, i, vec, limit)
        
        took += res["took"]
        recall += res["recall"]

    took = took / i
    recall = recall / i
    
    logger.info(
        f"mean={took}, qps={1/took}, recall={recall}, api={api}, ef={ef}, count={len(vectors)}"
    )

    results.append(
        {
            "efConstruction": efC,
            "maxConnections": m,
            "mean": took,
            "qps": 1 / took,
            "recall": recall,
            "shards": shards,
            "run_id": run_id,
            **labels,
        }
    )

    filename = f"./results/{run_id}.json"
    logger.info(f"storing results in {filename}")
    with open(filename, "w") as f:
        f.write(json.dumps(results))
    logger.info("done storing results")

# yellow = all primary shards ready; green = all primaries and replicas
#
# running in single node/shard yellow and green are equivalent since
# any replicas won't be able to be placed
def _wait_for_health_status(client, wait_seconds=30, status="yellow"):
    print("Waiting for Elasticsearch ...")
    for _ in range(wait_seconds):
        try:
            health = client.cluster.health(wait_for_status=status, request_timeout=1)
            print(f'Elasticsearch is ready: status={health["status"]}')
            return
        except ConnectionError:
            pass
        sleep(1)
    raise RuntimeError("Failed to connect to Elasticsearch")