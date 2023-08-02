import argparse
import weaviate
import sys
from loguru import logger
import h5py
import grpc
import pathlib
import time
import os

from weaviate_import import reset_schema, load_records
from weaviate_query import query

values = {
    "m": [16, 24, 32, 48],
    "shards": [3],
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
client = weaviate.Client(
    "http://localhost:8080", additional_config=weaviate.Config(grpc_port_experimental=50051)
)

stub = None

parser.add_argument("-v", "--vectors")
parser.add_argument("-d", "--distance")
parser.add_argument("-m", "--max-connections")
parser.add_argument("-l", "--labels")
parser.add_argument("-c", "--compression", action=argparse.BooleanOptionalAction)
parser.add_argument("-q", "--query-only", action=argparse.BooleanOptionalAction)
parser.add_argument("-o", "--override", action=argparse.BooleanOptionalAction)
parser.add_argument("-s", "--dim-to-segment-ratio")
parser.add_argument("--duplicates-count")
parser.add_argument("--duplicates-every")
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
values["duplicates"] = {
    "enabled": False,
    "count": 0,
    "every": 0,
}

if (args.dim_to_segment_ratio) != None:
    values["dim_to_segment_ratio"] = int(args.dim_to_segment_ratio)
    values["labels"]["dim_to_segment_ratio"] = values["dim_to_segment_ratio"]

if (args.duplicates_count) != None:
    values["duplicates"]["enabled"] = True
    values["duplicates"]["count"] = int(args.duplicates_count)
    values["duplicates"]["every"] = int(args.duplicates_every or 100_000)

f = h5py.File(args.vectors)
values["labels"]["dataset_file"] = os.path.basename(args.vectors)
vectors = f["train"]

efC = values["efC"]
distance = args.distance

for shards in values["shards"]:
    for m in values["m"]:
        if not values["query_only"]:
            compression = values["compression"]
            override = values["override"]
            duplicates = values["duplicates"]
            dim_to_seg_ratio = values["dim_to_segment_ratio"]
            logger.info(
                f"Starting import with efC={efC}, m={m}, shards={shards}, distance={distance}"
            )
            if override == False:
                reset_schema(client, efC, m, shards, distance)
            load_records(client, vectors, compression, dim_to_seg_ratio, override, duplicates)
            logger.info(f"Finished import with efC={efC}, m={m}, shards={shards}")
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
