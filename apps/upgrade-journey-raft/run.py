from loguru import logger
import weaviate
import books, multitenancy
from show_logs import show_logs


def create():
    try:
        logger.info("Connect to Weaviate")
        with weaviate.connect_to_local() as client:
            logger.info("Clean DB")
            client.collections.delete_all()
            logger.info("Run Multitenancy test suite")
            multitenancy.create(client)
            multitenancy.sanity_checks(client)
            logger.info("Run books test suite")
            books.create(client)
            books.sanity_checks(client)
        logger.success("Success")
    except:
        show_logs()
        raise Exception("Something went wrong")


if __name__ == "__main__":
    create()
