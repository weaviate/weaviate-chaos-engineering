from loguru import logger
import weaviate
import multitenancy
from show_logs import show_logs


def create():
    try:
        logger.info("Connect to Weaviate")
        with weaviate.connect_to_local() as client:
            logger.info("Add additional Multitenancy data")
            multitenancy.create_additional(client)
            multitenancy.sanity_checks_additional(client)
        logger.success("Success")
    except:
        show_logs()
        raise Exception("Something went wrong")


if __name__ == "__main__":
    create()
