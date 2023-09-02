import weaviate
from weaviate import Tenant
from loguru import logger
import time

total = 50_000
progress_report = 1_000
retries_on_error = 25


def run():
    client = weaviate.Client(
        url="http://localhost:8080",
    )

    client.schema.delete_all()

    client.schema.create_class({"class": "MT", "multiTenancyConfig": {"enabled": True}})

    for i in range(50_000):
        add_fn = lambda: client.schema.add_class_tenants(
            class_name="MT",  # The class to which the tenants will be added
            tenants=[Tenant(name=f"tenant_{i}")],
        )
        retry(add_fn, retries_on_error, 0.5)
        if i % progress_report == 0:
            logger.info(f"imported {i}/{total} tenants")

    logger.info(f"Success!")


def retry(fn, attempts, initial_backoff):
    last_error = None
    for i in range(attempts):
        try:
            fn()
            return
        except Exception as e:
            last_error = e

        delay = i * initial_backoff
        logger.warning(f"attempt {i}: request failed - trying again in {delay:.2f}s")
        time.sleep(delay)
    logger.error(f"request failed {attempts} times - giving up! last erorr: {last_error}")
    raise Exception(last_error)


run()
