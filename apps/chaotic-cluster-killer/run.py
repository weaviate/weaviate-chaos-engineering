import time
import sys
import os
import requests
import random

from loguru import logger

min_wait_for_kill = 10
max_wait_for_kill = 60

container_names = ["weaviate-node-2", "weaviate-node-3"]
ports = [8081, 8082]


def wait_all_healthy(max_wait):
    logger.info("waiting for all nodes to be healthy")
    before = time.time()

    while True:
        if time.time() - before > max_wait:
            logger.error(f"waited {max_wait}, still not all nodes healthy")
            sys.exit(1)

        if all_healthy():
            logger.info("all nodes are healthy")
            return

        time.sleep(3)


def all_healthy():
    for port in ports:
        try:
            res = requests.get(f"http://localhost:{port}/v1/")
            if res.status_code != 200:
                print(f"{port} not ready yet: {res.status_code}")
                return False
            else:
                continue
        except Exception as e:
            print(f"{port} not ready yet: {e}")
            return False
    return True


while True:
    wait_all_healthy(300)
    target = random.choice(container_names)
    wait_duration = random.randint(min_wait_for_kill, max_wait_for_kill)
    logger.info(f"wait {wait_duration} to kill {target}")
    time.sleep(wait_duration)
    logger.info(f"killing {target} now")
    os.system(
        f"docker-compose -f apps/weaviate/docker-compose-replication.yml kill {target} && docker-compose -f apps/weaviate/docker-compose-replication.yml up -d {target}"
    )
