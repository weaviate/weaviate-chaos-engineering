import argparse
import weaviate
import sys
from loguru import logger
import h5py
import grpc
import pathlib
import time
import os
from datetime import timedelta

from weaviate_import import reset_schema, load_records
from weaviate_query import query

values = {
    "m": [16, 24, 32, 48],
    "shards": [1],
    "efC": 256,
    "ef": [
        16,
        24,
        32,
        48,
        64,
        96,
        128,
        256,
        512,
    ],
    "compression": False,
    "dim_to_segment_ratio": 4,
    "override": False,
}

pathlib.Path("./results").mkdir(parents=True, exist_ok=True)


parser = argparse.ArgumentParser()
client = weaviate.connect_to_local()

stub = None

parser.add_argument("-v", "--vectors")
parser.add_argument("-d", "--distance")
parser.add_argument("-m", "--max-connections")
parser.add_argument("-l", "--labels")
parser.add_argument("-c", "--compression", action=argparse.BooleanOptionalAction)
parser.add_argument("-q", "--query-only", action=argparse.BooleanOptionalAction)
parser.add_argument("-o", "--override", action=argparse.BooleanOptionalAction)
parser.add_argument("-s", "--dim-to-segment-ratio")
args = parser.parse_args()


if (args.vectors) == None:
    logger.error(f"need -v or --vectors flag to point to dataset")
    sys.exit(1)

if (args.distance) == None:
    logger.error(f"need -d or --distance flag to indicate distance metric")
    sys.exit(1)

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

values["compression"] = args.compression or False
values["override"] = args.override or False
values["query_only"] = args.query_only
if (args.dim_to_segment_ratio) != None:
    values["dim_to_segment_ratio"] = int(args.dim_to_segment_ratio)
    values["labels"]["dim_to_segment_ratio"] = values["dim_to_segment_ratio"]

f = h5py.File(args.vectors)
values["labels"]["dataset_file"] = os.path.basename(args.vectors)
vectors = f["train"]

efC = values["efC"]
distance = args.distance

print(values["labels"])
for shards in values["shards"]:
    for m in values["m"]:
        if not values["query_only"]:
            compression = values["compression"]
            override = values["override"]
            dim_to_seg_ratio = values["dim_to_segment_ratio"]
            before_import = time.time()
            logger.info(
                f"Starting import with efC={efC}, m={m}, shards={shards}, distance={distance}"
            )
            if override == False:
                reset_schema(client, efC, m, shards, distance)
            load_records(client, vectors, compression, dim_to_seg_ratio, override)
            elapsed = time.time() - before_import
            logger.info(
                f"Finished import with efC={efC}, m={m}, shards={shards} in {str(timedelta(seconds=elapsed))}"
            )
            logger.info(f"Waiting 30s for compactions to settle, etc")
            time.sleep(30)

        logger.info(f"Starting querying for efC={efC}, m={m}, shards={shards}")
        query(
            client,
            stub,
            f,
            values["ef"],
            values["labels"],
        )
        logger.info(f"Finished querying for efC={efC}, m={m}, shards={shards}")
