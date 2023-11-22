import argparse
import weaviate
import sys
from loguru import logger
import h5py
import json
import pathlib
import time
import os
from datetime import timedelta

from weaviate_import import reset_schema, load_records

values = {
    "shards": [1],
    "efC": 256,
    "compression": False,
    "dim_to_segment_ratio": 4,
    "override": False,
    "batch_workers": [1, 4, 8, 16, 32],
    "dynamic": [True, False],
}

pathlib.Path("./results/batch-import-with-workers").mkdir(parents=True, exist_ok=True)

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
parser.add_argument("-a", "--async-on", action=argparse.BooleanOptionalAction)
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

async_on = args.async_on

results = []

print(values["labels"])
for shards in values["shards"]:
    for workers in values["batch_workers"]:
        for dynamic in values["dynamic"]:
            run_id = f"{int(time.time())}"

            compression = values["compression"]
            override = values["override"]
            before_import = time.time()
            logger.info(
                f"Starting import with efC={efC}, m={64}, shards={shards}, distance={distance}, num_workers={workers}, dynamic={dynamic}"
            )
            if override == False:
                reset_schema(client, efC, 64, shards, distance)
            load_records(client, vectors, compression, override, workers, dynamic)
            elapsed = time.time() - before_import
            logger.info(
                f"Finished import with efC={efC}, m={64}, shards={shards}, distance={distance}, num_workers={workers}, dynamic={dynamic} in {str(timedelta(seconds=elapsed))}hrs"
            )

            results.append(
                {
                    "num_workers": workers,
                    "import_time": elapsed,
                    "run_id": run_id,
                    "async_on": async_on,
                    "dynamic": dynamic,
                    **labels,
                }
            )

filename = f"./results/batch-import-with-workers/{run_id}.json"
logger.info(f"storing results in {filename}")
with open(filename, "w") as f:
    f.write(json.dumps(results))
logger.info("done storing results")
