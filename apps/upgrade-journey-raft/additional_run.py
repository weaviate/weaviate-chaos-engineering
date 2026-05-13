import argparse
import contextlib
from loguru import logger
import weaviate
import multitenancy
from show_logs import show_logs


def create(api_key=None, admin_api_key=None):
    try:
        with contextlib.ExitStack() as stack:
            logger.info("Connect to Weaviate")
            client = stack.enter_context(
                weaviate.connect_to_local(
                    auth_credentials=weaviate.auth.AuthApiKey(api_key=api_key) if api_key else None
                )
            )
            # See run.py: wait_for_vector_indexing() needs read_tenants on the target
            # collection, which the workload user lacks under RBAC. Open a separate admin
            # connection for the readiness wait when an admin key is supplied.
            if admin_api_key and admin_api_key != api_key:
                admin_client = stack.enter_context(
                    weaviate.connect_to_local(
                        auth_credentials=weaviate.auth.AuthApiKey(api_key=admin_api_key)
                    )
                )
            else:
                admin_client = client
            logger.info("Add additional Multitenancy data")
            multitenancy.create_additional(client, admin_client)
            multitenancy.sanity_checks_additional(client)
        logger.success("Success")
    except:
        show_logs()
        raise Exception("Something went wrong")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="API key for authentication", default=None)
    parser.add_argument(
        "--admin-api-key",
        help="Admin API key used only for privileged readiness waits under RBAC",
        default=None,
    )
    args = parser.parse_args()
    create(args.api_key, args.admin_api_key)
