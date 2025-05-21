import argparse
import weaviate
import sys
from loguru import logger
import h5py
import torch
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
    "quantization": False,
    "dim_to_segment_ratio": 4,
    "override": False,
    "multivector": False,
}

pathlib.Path("./results").mkdir(parents=True, exist_ok=True)


parser = argparse.ArgumentParser()
client = weaviate.connect_to_local()

stub = None

parser.add_argument("-v", "--vectors")
parser.add_argument("-d", "--distance")
parser.add_argument("-m", "--max-connections")
parser.add_argument("-l", "--labels")
parser.add_argument("-c", "--quantization")
parser.add_argument("-q", "--query-only", action=argparse.BooleanOptionalAction)
parser.add_argument("-o", "--override", action=argparse.BooleanOptionalAction)
parser.add_argument("-s", "--dim-to-segment-ratio")
parser.add_argument("-mv", "--multivector", action=argparse.BooleanOptionalAction, default=False)
parser.add_argument("-mi", "--multivector-implementation", default="regular")
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

values["quantization"] = args.quantization or False
values["override"] = args.override or False
values["query_only"] = args.query_only
if (args.dim_to_segment_ratio) != None:
    values["dim_to_segment_ratio"] = int(args.dim_to_segment_ratio)
    values["labels"]["dim_to_segment_ratio"] = values["dim_to_segment_ratio"]


values["multivector"] = args.multivector
values["multivector_implementation"] = args.multivector_implementation

# Add better error handling for file opening
try:
    # Check if file exists
    if not os.path.exists(args.vectors):
        logger.error(f"Dataset file does not exist: {args.vectors}")
        sys.exit(1)

    # Check if file is empty
    if os.path.getsize(args.vectors) == 0:
        logger.error(f"Dataset file is empty: {args.vectors}")
        sys.exit(1)

    logger.info(
        f"Opening dataset file: {args.vectors} (size: {os.path.getsize(args.vectors)} bytes)"
    )
    f = h5py.File(args.vectors)
    logger.info(f"Successfully opened dataset file")
except OSError as e:
    logger.error(f"Failed to open dataset file: {args.vectors}")
    logger.error(f"Error details: {str(e)}")
    logger.error(f"File exists: {os.path.exists(args.vectors)}")
    logger.error(
        f"File size: {os.path.getsize(args.vectors) if os.path.exists(args.vectors) else 'N/A'}"
    )
    logger.error(
        f"File permissions: {oct(os.stat(args.vectors).st_mode)[-3:] if os.path.exists(args.vectors) else 'N/A'}"
    )
    sys.exit(1)


values["labels"]["dataset_file"] = os.path.basename(args.vectors)
vectors = f["train"]
if values["multivector"]:
    vector_dim: int = 128
    vectors = [torch.from_numpy(sample.reshape(-1, vector_dim)) for sample in vectors]


efC = values["efC"]
distance = args.distance

print(values["labels"])
for shards in values["shards"]:
    for m in values["m"]:
        if not values["query_only"]:
            quantization = values["quantization"]
            override = values["override"]
            dim_to_seg_ratio = values["dim_to_segment_ratio"]
            multivector = values["multivector"]
            multivector_implementation = values["multivector_implementation"]
            before_import = time.time()
            logger.info(
                f"Starting import with efC={efC}, m={m}, shards={shards}, distance={distance}"
            )
            if override == False:
                reset_schema(
                    client, efC, m, shards, distance, multivector, multivector_implementation
                )
            load_records(
                client,
                vectors,
                quantization,
                dim_to_seg_ratio,
                override,
                multivector,
                multivector_implementation,
            )
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
            values["multivector"],
        )
        logger.info(f"Finished querying for efC={efC}, m={m}, shards={shards}")
