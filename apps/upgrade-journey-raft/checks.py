from loguru import logger
import weaviate
import books, multitenancy
from show_logs import show_logs


def sanity_checks():
    try:
        logger.info("Connect to Weaviate")
        with weaviate.connect_to_local() as client:
            logger.info("Run collection existence checks")
            multitenancy.check_collections_existence(client)
            logger.info("Run Multitenancy sanity checks")
            multitenancy.sanity_checks(client)
            logger.info("Run books sanity checks")
            books.sanity_checks(client)
        logger.success("Success")
    except:
        show_logs()
        raise Exception("Something went wrong")


if __name__ == "__main__":
    sanity_checks()
