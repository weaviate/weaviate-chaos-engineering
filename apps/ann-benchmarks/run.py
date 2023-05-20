import argparse
import weaviate
import sys
from loguru import logger
import h5py
import grpc
import pathlib

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
args = parser.parse_args()


if (args.vectors) == None:
    logger.error(f"need -v or --vectors flag to point to dataset")
    sys.exit(1)

if (args.distance) == None:
    logger.error(f"need -d or --distance flag to indicate distance metric")
    sys.exit(1)

if (args.max_connections) != None:
    values["m"] = [int(x) for x in args.max_connections.split(",")]

if (args.labels) != None:
    pairs = [l for l in args.labels.split(",")]
    labels = {}
    for pair in pairs:
        kv = pair.split("=")
        if len(kv) != 2:
            logger.error(f"invalid labels, must be in format key_1=value_2,key_2=value_2")
        labels[kv[0]] = kv[1]
    values["labels"] = labels

values["compression"] = args.compression
values["query_only"] = args.query_only
print(values["labels"])

f = h5py.File(args.vectors)
vectors = f["train"]

efC = values["efC"]
distance = args.distance

for shards in values["shards"]:
    for m in values["m"]:
        if not values["query_only"]:
            compression = values["compression"]
            logger.info(
                f"Starting import with efC={efC}, m={m}, shards={shards}, distance={distance}"
            )
            reset_schema(client, efC, m, shards, distance)
            load_records(client, vectors, compression)
            logger.info(f"Finished import with efC={efC}, m={m}, shards={shards}")

        logger.info(f"Starting querying for efC={efC}, m={m}, shards={shards}")
        query(
            client,
            stub,
            f,
            values["ef"],
            values["labels"],
        )
        logger.info(f"Finished querying for efC={efC}, m={m}, shards={shards}")
