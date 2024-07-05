from wonderwords import RandomSentence
import weaviate
import weaviate.classes as wvc
from loguru import logger
import random
import time
import os, sys
import argparse
import psutil, signal
import concurrent
import subprocess

s = RandomSentence()

# Initialize the global singleton
cfg = None
client = None


cycles = 10
objects_per_cycle = 50_000
updates_per_cycle = 1_000
validate_queries = 5_000

# the wonderwords library is fairly slow to create sentences. Therefore we're
# not creating them on the fly. Let's pre-create some sentences and then pick
# random combinations of sentences at import time
sentences = [s.sentence() for i in range(10000)]


def reset_schema(cycle: int) -> weaviate.collections.Collection:
    if cfg.skip_schema:
        logger.info(f"Skipping schema delete/create in cycle {cycle}")
        col = client.collections.get("Book")
        return col
    else:
        client.collections.delete_all()
        col = client.collections.create(
            "Book",
            replication_config=wvc.config.Configure.replication(factor=int(cfg.replication_factor)),
            sharding_config=wvc.config.Configure.sharding(desired_count=1),
            properties=[
                wvc.config.Property(name="random_number", data_type=wvc.config.DataType.INT)
            ],
        ).with_consistency_level(wvc.ConsistencyLevel.ONE)
        logger.info(f"Reset schema in cycle {cycle}")
        return col


def run():
    for cycle in range(cycles):
        col = reset_schema(cycle)
        import_data(col, cycle)

        max_pause_before_kill = 10
        kill_fn = kill_weaviate_local
        if cfg.mode == "docker":
            kill_fn = kill_weaviate_docker
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_del = executor.submit(delete_data, col, cycle)
            future_kill = executor.submit(kill_fn, max_pause_before_kill)
            concurrent.futures.wait([future_del, future_kill])

        # assume that a crash occured during the delete cycle, wait for the
        # setup to be back and fully responsive (in case of single-node setup)
        wait_for_ready()

        update_data(col, cycle)
        validate_data(col, cycle)


def wait_for_ready():
    max_wait = 60
    for i in range(max_wait):
        try:
            if client.is_ready():
                logger.info(f"setup is back up after {i} seconds, continue")
                return
        except Exception as e:
            pass

        logger.warning(f"waited {i} seconds, setup not back up yet")
        time.sleep(1)

    logger.error(f"setup did not come back in {max_wait} seconds.")
    sys.exit(1)


def import_data(col: weaviate.collections.Collection, cycle: int):
    logger.info(f"Start import cycle {cycle}")
    with col.batch.fixed_size(250, 8) as batch:
        for j in range(objects_per_cycle):
            batch.add_object(
                {
                    "title": random.choice(sentences),
                    "description": "".join([random.choice(sentences) for i in range(50)]),
                    "constant_word": "potato",
                    "random_number": random.randint(0, 1000),
                }
            )
            if j % 1_000 == 0:
                logger.info(f"cycle {cycle}: object {j}")


def delete_data(col: weaviate.collections.Collection, cycle: int):
    logger.info(f"Start delete cycle {cycle}")
    # delete the objects with the smallest "random_number" field of less than
    # 200. Should be about 20% of data on average.

    try:
        res = col.data.delete_many(
            where=wvc.query.Filter.by_property("random_number").less_than(200)
        )
    except Exception as e:
        logger.error(e)
        # the most common failure type is that a node is down which is to be
        # expected as we are inducing crashes all the time. In this case let's
        # wait for a bit so we don't just race through all requests and they
        # fail immediately.
        time.sleep(1)


def update_data(col: weaviate.collections.Collection, cycle: int):
    logger.info(f"Start update cycle {cycle}")
    try:
        res = col.query.fetch_objects(
            limit=updates_per_cycle,
            filters=wvc.query.Filter.by_property("random_number").greater_or_equal(
                random.randint(0, 1000)
            ),
        )
        ids = [obj.uuid for obj in res.objects]
        for i, uuid in enumerate(ids):
            col.data.update(
                uuid,
                {
                    "description": "".join([random.choice(sentences) for i in range(50)]),
                },
            )

            if i % 250 == 0:
                logger.info(f"Updated {i}/{len(ids)} objects")
    except Exception as e:
        logger.error(e)
        # the most common failure type is that a node is down which is to be
        # expected as we are inducing crashes all the time. In this case let's
        # wait for a bit so we don't just race through all requests and they
        # fail immediately.
        time.sleep(1)


def validate_data(col: weaviate.collections.Collection, cycle: int):
    try:
        logger.info(f"collection now has {len(col)} objects")
    except Exception as e:
        logger.error(e)
    for j in range(validate_queries):
        try:
            rand_bool = random.random() > 0.5
            query_properties = None  # by default do a BM25F query on random fields
            query_properties_static = None  # by default do a BM25F query on static field
            query = ""  # so we can print it in case of failure

            r = random.random()
            if r < 0.25:
                query_properties = ["title"]
                query_properties_static = ["constant_word"]
            elif r < 0.5:
                query_properties = ["description"]
                query_properties_static = ["constant_word"]

            if j < 100:  # run multiple times, so we hit each replica
                query = "potato"
                res = col.query.bm25(
                    query=query,
                    return_metadata=wvc.query.MetadataQuery(explain_score=rand_bool),
                    query_properties=query_properties_static,
                )

            rand_bool = False  # so we can print which was the last query to run in case of failure
            query = random.choice(sentences)
            res = col.query.bm25(
                query=query,
                return_metadata=wvc.query.MetadataQuery(explain_score=rand_bool),
                query_properties=query_properties,
            )
            if j % 100 == 0:
                logger.info(f"validated {j}")
        except Exception as e:
            # consider any error on query a reason to fail the script. This
            # could be an explicitly returned error, or this could be the
            # server crashing and not being able to honor the request at all.
            logger.error(e)
            logger.info(
                f"params on failed query: static_query={rand_bool} props_static={query_properties_static} props={query_properties} query={query}"
            )
            sys.exit(1)


# Kills Weaviate in case the server is running locally, searches for PIDs on
# the local system. This cannot be used when Weaviate is running in Docker
# instead.
def kill_weaviate_local(max_pause):
    pause = random.random() * max_pause
    logger.info(f"Killing Weaviate local process in {pause:.2f}s...")
    time.sleep(pause)

    # Get a list of all running processes
    all_processes = psutil.process_iter()

    # Filter processes containing "weaviate-server" in their name
    weaviate_processes = [
        process for process in all_processes if "weaviate-server" in process.name().lower()
    ]

    if weaviate_processes:
        # Assume there is only one weaviate process, extract its PID
        weaviate_pid = weaviate_processes[0].pid
        logger.info(f"Found weaviate-server process with PID: {weaviate_pid}")

        # Send a SIGKILL (9) to the weaviate process
        try:
            os.kill(weaviate_pid, signal.SIGKILL)
            logger.info(f"Sent SIGKILL to weaviate-server process with PID: {weaviate_pid}")
        except ProcessLookupError as e:
            logger.error(e)
    else:
        logger.error("No weaviate-server process found.")


def kill_weaviate_docker(max_pause):
    pause = random.random() * max_pause
    logger.info(f"Killing Weaviate docker container in {pause:.2f}s...")
    time.sleep(pause)

    try:
        ps_command = "docker ps -q --filter=name=weaviate"
        container_ids = (
            subprocess.check_output(ps_command, shell=True, text=True).strip().split("\n")
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Error finding container IDs: {e}")
        return

    for container_id in container_ids:
        try:
            subprocess.run(["docker", "restart", "--signal", "SIGKILL", container_id], check=True)
            logger.info(f"Restarted Docker container '{container_id}' using SIGKILL")
        except subprocess.CalledProcessError as e:
            logger.error(e)


def configure_logger():
    logger.remove()  # Remove the default logger
    logger.add(
        sys.stderr,
        format="<green>{elapsed}</green> | <level>{level: <8}</level> | <level> {message} </level>",
    )


class Config:
    def __init__(self, skip_schema, host, replication_factor, mode):
        self.skip_schema = skip_schema
        self.host = host
        self.replication_factor = replication_factor
        self.mode = mode


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Kill Weaviate during batch deletes and validate BM25 still works afterwards"
    )
    parser.add_argument("--skip-schema", action="store_true", help="Skip processing schema if set")
    parser.add_argument("--host", default="localhost", help="Specify the host (default: localhost)")
    parser.add_argument(
        "--replication-factor",
        type=int,
        default=1,
        help="Specify the replication factor (default: 1)",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["local", "docker"],
        help="Specify the mode (required, choose from: local, docker)",
    )

    args = parser.parse_args()
    return Config(
        skip_schema=args.skip_schema,
        host=args.host,
        replication_factor=args.replication_factor,
        mode=args.mode,
    )


if __name__ == "__main__":
    configure_logger()
    cfg = parse_arguments()
    client = weaviate.connect_to_local(host=cfg.host)
    run()
