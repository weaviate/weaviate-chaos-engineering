import argparse
from loguru import logger
from show_logs import show_logs
import weaviate

import defaults


def _verify_roles(client: weaviate.WeaviateClient):
    roles = client.roles.list_all()
    role_names = roles.keys()

    expected_roles = {
        **defaults.EXPECTED_ROLES,
        **defaults.ADDITIONAL_ASSIGNED_ROLES,
        **defaults.ADDITIONAL_UNASSIGNED_ROLES,
    }
    # First check if all expected roles exist
    if not all(role in role_names for role in expected_roles.keys()):
        raise Exception("Some roles do not exist")

    # Then verify permissions for each role
    for role_name, expected_permissions in expected_roles.items():
        for permission in expected_permissions:
            if not client.roles.has_permissions(permissions=permission, role=role_name):
                raise Exception(f"Role {role_name} is missing expected permissions")

    logger.success("All roles exist with correct permissions")

    expected_assigned_roles = {**defaults.ADDITIONAL_ASSIGNED_ROLES, **defaults.EXPECTED_ROLES}
    roles = list(expected_assigned_roles.keys())
    roles_assigned = client.users.get_assigned_roles(user_id=defaults.CUSTOM_USER).keys()
    if not all(role in roles_assigned for role in roles):
        raise Exception("Some roles are not assigned")

    logger.success("All roles are assigned")


def check_roles(api_key=None):
    try:
        logger.info("Connect to Weaviate")
        with weaviate.connect_to_local(
            auth_credentials=weaviate.AuthApiKey(api_key=api_key) if api_key else None
        ) as client:
            logger.info("Verify roles")
            _verify_roles(client)
        logger.success("Success")
    except:
        show_logs()
        raise Exception("Something went wrong")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="API key for authentication", default=None)
    args = parser.parse_args()
    check_roles(args.api_key)
