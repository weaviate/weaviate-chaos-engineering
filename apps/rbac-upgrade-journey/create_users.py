import argparse
import time
from loguru import logger
from show_logs import show_logs
import weaviate
import defaults


def _create_users(client: weaviate.WeaviateClient):
    users = []
    for i in range(defaults.NUMBER_OF_USERS):
        user = f"{defaults.USER_PREFIX}-{i}"
        client.users.db.create(user_id=user)
        users.append(user)
    return users


def _assign_roles(client: weaviate.WeaviateClient, users: list):
    roles = list(defaults.ADDITIONAL_ASSIGNED_ROLES.keys())
    for user in users:
        client.users.db.assign_roles(
            user_id=user,
            role_names=roles,
        )

        sec, cutoff = 0, 120
        roles_assigned = False
        while sec < cutoff:
            sec = sec + 1
            current_roles = client.users.db.get_assigned_roles(user_id=user).keys()
            if all(role in current_roles for role in roles):
                logger.info(f"All roles assigned to user {user}, proceeding with checks")
                roles_assigned = True
                break
            logger.warning(f"Not all roles assigned to user {user}, waiting 1s to retry...")
            time.sleep(1)

        if not roles_assigned:
            raise Exception(
                f"Role assignment check timed out after {cutoff}s, roles not assigned to user {user}"
            )


def create_users(api_key=None):
    try:
        logger.info("Connect to Weaviate")
        with weaviate.connect_to_local(
            auth_credentials=weaviate.AuthApiKey(api_key=api_key) if api_key else None
        ) as client:
            logger.info("Run users creation")
            users = _create_users(client)
            logger.info("Run roles assignment")
            _assign_roles(client, users)
        logger.success("Success")
    except:
        show_logs()
        raise Exception("Something went wrong")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="API key for authentication", default=None)
    args = parser.parse_args()
    create_users(args.api_key)
