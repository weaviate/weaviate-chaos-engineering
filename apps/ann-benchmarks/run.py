import argparse
import weaviate
import sys
from loguru import logger
import h5py
import grpc

from weaviate_import import reset_schema, load_records
from weaviate_query import query

from weaviategrpc import weaviate_pb2_grpc, weaviate_pb2

values = {
    "m": [16, 24, 32, 48],
    "shards": [1],
    "efC": 512,
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
}

values["ef"].reverse()


parser = argparse.ArgumentParser()
client = weaviate.Client("http://localhost:8080")

channel = grpc.insecure_channel("localhost:50051")
stub = weaviate_pb2_grpc.WeaviateStub(channel)

parser.add_argument("-v", "--vectors")
parser.add_argument("-d", "--distance")
parser.add_argument("-m", "--max-connections")
args = parser.parse_args()


if (args.vectors) == None:
    logger.error(f"need -v or --vectors flag to point to dataset")
    sys.exit(1)

if (args.distance) == None:
    logger.error(f"need -d or --distance flag to point to dataset")
    sys.exit(1)

if (args.max_connections) != None:
    values["m"] = [int(x) for x in args.max_connections.split(",")]

f = h5py.File(args.vectors)
vectors = f["train"]

efC = values["efC"]
distance = args.distance

for shards in values["shards"]:
    for m in values["m"]:
        logger.info(f"Starting import with efC={efC}, m={m}, shards={shards}, distance={distance}")
        reset_schema(client, efC, m, shards, distance)
        load_records(client, vectors)
        logger.info(f"Finished import with efC={efC}, m={m}, shards={shards}")

        logger.info(f"Starting querying for efC={efC}, m={m}, shards={shards}")
        query(
            client,
            stub,
            f,
            values["ef"],
        )
        logger.info(f"Finished querying for efC={efC}, m={m}, shards={shards}")
