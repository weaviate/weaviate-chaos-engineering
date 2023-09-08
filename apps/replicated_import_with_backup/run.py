import datetime
import os
import sys
import time
import weaviate

from loguru import logger


def config_host() -> str:
    host = os.environ.get("CONFIG_HOST")
    if host is None or host == "":
        return "http://localhost:8080"
    return host


def config_backup_backend() -> str:
    backend = os.environ.get("CONFIG_BACKUP_BACKEND")
    if backend is None or backend == "":
        return "s3"
    return backend


def create_backup(client: weaviate.Client, backup_id: str, backend: str):
    res = client.backup.create(backup_id, backend)
    if res["status"] == "SUCCESS":
        logger.success(f"Backup creation successful")
        return

    # timeout of 60 minutes (60 * (20*3))
    checks = 60
    for i in range(checks):
        for j in range(20):
            time.sleep(3)
            res = client.backup.get_create_status(backup_id, backend)
            if res["status"] == "SUCCESS":
                logger.success(f"Backup creation successful")
                return
            if res["status"] == "FAILED":
                logger.error(f"Backup failed with res: {res}")
                sys.exit(1)
        logger.info(f"Backup creation status check {i+1}/{checks}. Last res {res}")
    logger.error(f"Backup failed due to timeout. Last res: {res}")
    sys.exit(1)


if __name__ == "__main__":
    host = config_host()
    backup_backend = config_backup_backend()
    sleep = 5

    client = weaviate.Client(host)

    logger.info(f"CONFIG: host={host}; backup_backend={backup_backend}; sleep={sleep}")
    i = 1
    while True:
        time.sleep(sleep)
        backup_id = f"backup_{i}"

        logger.info(f'Backup "{backup_id}" started at {datetime.datetime.now()}')
        create_backup(client, backup_id, backup_backend)
        logger.info(f'Backup "{backup_id}" finished at {datetime.datetime.now()}')
        i = i + 1
