import argparse
from loguru import logger
import weaviate
import books, multitenancy
from show_logs import show_logs


def create(api_key=None):
    try:
        logger.info("Connect to Weaviate")
        with weaviate.connect_to_local(
            auth_credentials=weaviate.auth.AuthApiKey(api_key=api_key) if api_key else None
        ) as client:
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="API key for authentication", default=None)
    args = parser.parse_args()
    create(args.api_key)
