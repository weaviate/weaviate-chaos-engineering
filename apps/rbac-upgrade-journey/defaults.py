#! /usr/bin/env python3
from weaviate.rbac.models import Permissions

ADMIN_USER = "admin-user"
CUSTOM_USER = "custom-user"
ST_COLLECTION_PREFIX = "Books"
ST_COLLECTION_REF = "Authors"
MT_COLLECTION_PREFIX = "MTClass"
TENANT_PREFIX = "tenant"
USER_PREFIX = "user"
NUMBER_OF_USERS = 10
EXPECTED_ROLES = {
    "CollectionST": [
        Permissions.collections(
            collection=f"{ST_COLLECTION_PREFIX}*",
            create_collection=True,
            read_config=True,
            update_config=True,
            delete_collection=True,
        )
    ],
    "CollectionSTRef": [
        Permissions.collections(
            collection=f"{ST_COLLECTION_REF}*",
            create_collection=True,
            read_config=True,
            update_config=True,
            delete_collection=True,
        )
    ],
    "CollectionMT": [
        Permissions.collections(
            collection=f"{MT_COLLECTION_PREFIX}*",
            create_collection=True,
            read_config=True,
            update_config=True,
            delete_collection=True,
        )
    ],
    "DataST": [
        Permissions.data(
            collection=f"{ST_COLLECTION_PREFIX}*",
            read=True,
            create=True,
            update=True,
            delete=True,
        )
    ],
    "DataSTRef": [
        Permissions.data(
            collection=f"{ST_COLLECTION_REF}*",
            read=True,
            create=True,
            update=True,
            delete=True,
        )
    ],
    "DataMT": [
        Permissions.data(
            collection=f"{MT_COLLECTION_PREFIX}*",
            read=True,
            create=True,
            update=True,
            delete=True,
        )
    ],
    "TenantsMT": [
        Permissions.tenants(
            collection=f"{MT_COLLECTION_PREFIX}*",
            read=True,
            create=True,
            update=True,
            delete=True,
        )
    ],
    "Role": [
        Permissions.users(
            user="user*",
            assign_and_revoke=True,
        )
    ],
}

ADDITIONAL_ASSIGNED_ROLES = {
    "CollectionRead": [
        Permissions.collections(
            collection=[f"{ST_COLLECTION_PREFIX}*", f"{MT_COLLECTION_PREFIX}*"],
            read_config=True,
        )
    ],
    "CollectionWrite": [
        Permissions.collections(
            collection=[f"{ST_COLLECTION_PREFIX}*", f"{MT_COLLECTION_PREFIX}*"],
            update_config=True,
        )
    ],
    "CollectionDelete": [
        Permissions.collections(
            collection=[f"{ST_COLLECTION_PREFIX}*", f"{MT_COLLECTION_PREFIX}*"],
            delete_collection=True,
        )
    ],
    "DataRead": [
        Permissions.data(
            collection=[f"{ST_COLLECTION_PREFIX}*", f"{MT_COLLECTION_PREFIX}*"],
            read=True,
        )
    ],
    "DataWrite": [
        Permissions.data(
            collection=[f"{ST_COLLECTION_PREFIX}*", f"{MT_COLLECTION_PREFIX}*"],
            update=True,
        )
    ],
    "DataDelete": [
        Permissions.data(
            collection=[f"{ST_COLLECTION_PREFIX}*", f"{MT_COLLECTION_PREFIX}*"],
            delete=True,
        )
    ],
}

ADDITIONAL_UNASSIGNED_ROLES = {
    "Unassigned_1": [
        Permissions.collections(collection="*", read_config=True),
        Permissions.data(collection="*", read=True),
        Permissions.tenants(collection="*", read=True),
        Permissions.backup(collection="*", manage=True),
        Permissions.cluster(read=True),
        Permissions.Nodes.verbose(collection="*", read=True),
        Permissions.roles(role="*", read=True),
    ],
    "Unassigned_2": [
        Permissions.collections(collection=""),
        Permissions.data(collection=""),
        Permissions.tenants(collection=""),
        Permissions.backup(collection=""),
        Permissions.cluster(),
        Permissions.Nodes.verbose(collection=""),
        Permissions.roles(role=""),
    ],
}
