import time
import argparse
from loguru import logger
from show_logs import show_logs
import weaviate
from weaviate.rbac.models import (
    Permissions,
)
import defaults


def _create_roles(client: weaviate.WeaviateClient):

    additional_roles = {
        **defaults.ADDITIONAL_UNASSIGNED_ROLES,
        **defaults.ADDITIONAL_ASSIGNED_ROLES,
    }
    for role, permissions in additional_roles.items():
        client.roles.create(
            role_name=role,
            permissions=permissions,
        )
    expected_additional_role_names = list(additional_roles.keys())
    sec, cutoff = 0, 120
    while sec < cutoff:
        sec = sec + 1
        roles = client.roles.list_all()
        role_names = roles.keys()
        if all(role in role_names for role in expected_additional_role_names):
            logger.info("All roles exist, proceeding with checks")
            return
        logger.warning("Not all roles exist, waiting 1s to retry...")
        time.sleep(1)

    raise Exception(f"Role check timed out after {cutoff}s, roles don't exist")


def _assign_roles(client: weaviate.WeaviateClient):

    roles = list(defaults.ADDITIONAL_ASSIGNED_ROLES.keys())
    client.users.assign_roles(
        user_id=defaults.CUSTOM_USER,
        role_names=roles,
    )

    sec, cutoff = 0, 120
    while sec < cutoff:
        sec = sec + 1
        roles_assigned = client.users.get_assigned_roles(user_id=defaults.CUSTOM_USER).keys()
        if all(role in roles_assigned for role in roles):
            logger.info("All roles assigned, proceeding with checks")
            return
        logger.warning("Not all roles assigned, waiting 1s to retry...")
        time.sleep(1)

    raise Exception(f"Role assignment check timed out after {cutoff}s, roles not assigned")


def create_roles(api_key=None):
    try:
        logger.info("Connect to Weaviate")
        with weaviate.connect_to_local(
            auth_credentials=weaviate.AuthApiKey(api_key=api_key) if api_key else None
        ) as client:
            logger.info("Run roles creation")
            _create_roles(client)
            logger.info("Run roles assignment")
            _assign_roles(client)
        logger.success("Success")
    except:
        show_logs()
        raise Exception("Something went wrong")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="API key for authentication", default=None)
    args = parser.parse_args()
    create_roles(args.api_key)
