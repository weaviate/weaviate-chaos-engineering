# Script to check if users were created successfully

import argparse
from loguru import logger
from show_logs import show_logs
import weaviate

import defaults


def _check_users(client: weaviate.WeaviateClient):
    users = [user.user_id for user in client.users.db.list_all() if user.user_id.startswith("user")]
    if len(users) != defaults.NUMBER_OF_USERS:
        raise Exception(f"Expected {defaults.NUMBER_OF_USERS} users, got {len(users)}")
    logger.success("Success")

    roles = list(defaults.ADDITIONAL_ASSIGNED_ROLES.keys())
    for user in users:
        roles_assigned = client.users.db.get_assigned_roles(user_id=user).keys()
        if not all(role in roles_assigned for role in roles):
            raise Exception(f"User {user} does not have all roles assigned")
    logger.success("Success")


def check_users(api_key=None):
    try:
        logger.info("Connect to Weaviate")
        with weaviate.connect_to_local(
            auth_credentials=weaviate.AuthApiKey(api_key=api_key) if api_key else None
        ) as client:
            logger.info("Check users")
            _check_users(client)
        logger.success("Success")
    except:
        show_logs()
        raise Exception("Something went wrong")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="API key for authentication", default=None)
    args = parser.parse_args()
    check_users(args.api_key)
