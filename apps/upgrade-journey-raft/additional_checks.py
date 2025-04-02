from loguru import logger
import weaviate
import books, multitenancy
from show_logs import show_logs
import argparse


def sanity_checks(api_key=None):
    try:
        logger.info("Connect to Weaviate")
        with weaviate.connect_to_local(
            auth_credentials=weaviate.auth.AuthApiKey(api_key=api_key) if api_key else None
        ) as client:
            logger.info("Check Multitenancy collection existence")
            multitenancy.check_additional_collections_existence(client)
            logger.info("Run Multitenancy sanity checks")
            multitenancy.sanity_checks(client)
            logger.info("Run Multitenancy Additional sanity checks")
            multitenancy.sanity_checks_additional(client)
            logger.info("Run books sanity checks")
            books.sanity_checks(client)
        logger.success("Success")
    except:
        show_logs()
        raise Exception("Something went wrong")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="API key for authentication", default=None)
    args = parser.parse_args()
    sanity_checks(args.api_key)
