import argparse
from elasticsearch import ConnectionError, Elasticsearch
from elasticsearch.helpers import bulk
import sys
from loguru import logger
import h5py
import pathlib
import time
import os

from es_import import reset_schema, load_records
from es_query import query

values = {
    "m": [16, 24, 32, 48],
    "shards": [1],
    "efC": 256,
    "labels": {}
}

pathlib.Path("./results").mkdir(parents=True, exist_ok=True)

parser = argparse.ArgumentParser()
client = Elasticsearch(["http://localhost:9200"])

parser.add_argument("-v", "--vectors")
parser.add_argument("-d", "--distance")
parser.add_argument("-m", "--max-connections")
parser.add_argument("-l", "--labels")
parser.add_argument("-n", "--dimensions")
parser.add_argument("-a", "--metric")
parser.add_argument("-q", "--query-only", action=argparse.BooleanOptionalAction)
args = parser.parse_args()

if (args.vectors) == None:
    logger.error(f"need -v or --vectors flag to point to dataset")
    sys.exit(1)

if (args.distance) == None:
    logger.error(f"need -d or --distance flag to indicate distance metric [l2_norm|cosine]")
    sys.exit(1)

if (args.dimensions) == None:
    logger.error(f"need -n or --dimensions flag to indicate dimension count")
    sys.exit(1)
else:
    values["dimensions"] = args.dimensions

if (args.max_connections) != None:
    values["m"] = [int(x) for x in args.max_connections.split(",")]

labels = {}
if (args.labels) != None:
    pairs = [l for l in args.labels.split(",")]
    for pair in pairs:
        kv = pair.split("=")
        if len(kv) != 2:
            logger.error(f"invalid labels, must be in format key_1=value_2,key_2=value_2")
        labels[kv[0]] = kv[1]
    values["labels"] = labels

values["query_only"] = args.query_only

f = h5py.File(args.vectors)
values["labels"]["dataset_file"] = os.path.basename(args.vectors)
vectors = f["train"]

efC = values["efC"]
distance = args.distance
dimensions = values["dimensions"]

print(values["labels"])
for shards in values["shards"]:
    for m in values["m"]:
        index_name = f"{distance}-{dimensions}-{efC}-{m}"
        if not values["query_only"]:
            logger.info(
                f"Starting import with efC={efC}, m={m}, shards={shards}, distance={distance}, dimensions={dimensions}"
            )
            reset_schema(client, index_name, efC, m, shards, distance, dimensions)
            load_records(client, index_name, vectors)
            logger.info(f"Finished import with efC={efC}, m={m}, shards={shards}, dimensions={dimensions}")
            logger.info(f"Waiting 30s to settle")
            time.sleep(30)

        logger.info(f"Starting querying for efC={efC}, m={m}, shards={shards}")
        query(client, index_name, f, values["labels"])
        logger.info(f"Finished querying for efC={efC}, m={m}, shards={shards}")