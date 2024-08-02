import argparse
import weaviate
import weaviate.classes as wvc
import weaviate.classes.config as wvcc
import uuid
import numpy as np
from loguru import logger
from typing import List
import random
import time
from wonderwords import RandomSentence
from wonderwords import RandomWord

s = RandomSentence()
w = RandomWord()

weaviate_nodes = 3
deactivation_percentage = 0.8
tenants = 100
tenants_per_iteration = 10
min_objects_per_tenant = 1000
max_objects_per_tenant = 10000


def import_phase_1():
    # Phase 1 generally runs in batches of tenants. In iteration n we import
    # into tenants n*t to (n+1)*t. Then in interation n+1 we deactivate a
    # random subset of tenants n*t to (n+1)*t. This way there is some time
    # between the initial import and the deactivation of the tenants. This
    # allows for some time to run background processes, such as compactions,
    # etc. The randomness should make sure that we have tenants in every state,
    # some will have been perfectly compacted, others will have had no chance
    # to compact at all.
    clients = create_clients()
    reset_schema(clients)

    logger.info(f"Generate sentences upfront")
    before = time.time()
    sentences = [s.sentence() for _ in range(10000)]
    took = time.time() - before
    logger.info(f"Generated 10000 sentences in {took:.2f} seconds")

    for i in range(0, tenants, tenants_per_iteration):
        start_tenant_id = i
        end_tenant_id = min(i + tenants_per_iteration, tenants)
        for j in range(start_tenant_id, end_tenant_id):
            before = time.time()
            tenant_name = f"{j:06d}"
            client = random.choice(clients)
            col = client.collections.get("DataPoint").with_tenant(tenant_name)
            col.tenants.create([wvc.tenants.Tenant(name=tenant_name)])
            obj_count = random.randint(min_objects_per_tenant, max_objects_per_tenant)
            with col.batch.dynamic() as batch:
                for obj_id in range(obj_count):
                    obj = gen_object(obj_id, sentences)
                    batch.add_object(
                        properties=obj[0],
                        uuid=obj[1],
                        vector=obj[2],
                    )
            took = time.time() - before
            logger.info(
                f"Imported {obj_count} objects into tenant {tenant_name} in {took:.2f} seconds"
            )
        deactivate_tenants(clients, start_tenant_id, end_tenant_id)

    for client in clients:
        client.close()


def gen_object(id: int, sentences: List[str]) -> (dict, uuid.UUID, list):
    return (
        {
            "obj_id": id,
            "paragraph": "".join(random.choices(sentences, k=20)),
            "sentence": random.choice(sentences),
            "number": random.randint(0, 100),
            "large_number": random.randint(0, 1000000),
        },
        uuid.UUID(int=id),
        np.random.rand(1, 1536)[0].tolist(),
    )


def import_phase_2():
    # In this phase we try to touch as many tenants as possible. We might
    # import as little as a few objects per tenant, but we will import into
    # many tenants. This should trigger a lot of tenant activation events.
    clients = create_clients()

    logger.info(f"Generate sentences upfront")
    before = time.time()
    sentences = [s.sentence() for _ in range(10000)]
    took = time.time() - before
    logger.info(f"Generated 10000 sentences in {took:.2f} seconds")

    before = time.time()
    for i in range(10_000):
        tenant_id = random.randint(0, tenants)
        tenant_name = f"{tenant_id:06d}"
        client = random.choice(clients)
        col = client.collections.get("DataPoint").with_tenant(tenant_name)
        with col.batch.dynamic() as batch:
            # this convention can easily tell us if an object was imported in phase 1 or 2
            obj_id = 1_000_000_000 + i

            obj = gen_object(i, sentences)
            batch.add_object(
                properties=obj[0],
                uuid=obj[1],
                vector=obj[2],
            )

        if len(col.batch.failed_objects) > 0:
            logger.error(
                f"Failed to import object {obj_id} into tenant {tenant_name}: {col.batch.failed_objects}"
            )

        # deactivate tenant again, so a future write requires another tenant activation
        col.tenants.update(
            [
                wvc.tenants.Tenant(
                    name=tenant_name, activity_status=wvc.tenants.TenantActivityStatus.COLD
                )
            ]
        )
        if i % 10 == 0 and i > 0:
            took = time.time() - before
            before = time.time()
            logger.info(f"Imported 10 additional objects in {took:.2f} seconds")

    for client in clients:
        client.close()


def create_clients() -> List[weaviate.WeaviateClient]:
    return [
        weaviate.connect_to_local(port=8080 + i, grpc_port=50051 + i) for i in range(weaviate_nodes)
    ]


def reset_schema(clients: List[weaviate.WeaviateClient]):
    client = random.choice(clients)
    client.collections.delete_all()
    client.collections.create(
        "DataPoint",
        multi_tenancy_config=wvcc.Configure.multi_tenancy(
            enabled=True, auto_tenant_activation=True, auto_tenant_creation=True
        ),
        vector_index_config=wvcc.Configure.VectorIndex.flat(
            quantizer=wvcc.Configure.VectorIndex.Quantizer.bq(cache=True)
        ),
        replication_config=wvcc.Configure.replication(factor=2),
    )


def deactivate_tenants(
    clients: List[weaviate.WeaviateClient], start_tenant_id: int, end_tenant_id: int
):
    client = random.choice(clients)
    col = client.collections.get("DataPoint")

    tenants_to_deactivate: List[wvc.tenants.Tenant] = []
    for i in range(start_tenant_id, end_tenant_id):
        tenant_name = f"{i:06d}"
        if random.random() < deactivation_percentage:
            tenants_to_deactivate.append(
                wvc.tenants.Tenant(
                    name=tenant_name, activity_status=wvc.tenants.TenantActivityStatus.COLD
                )
            )

    col.tenants.update(tenants_to_deactivate)
    logger.info(f"Deactivated {len(tenants_to_deactivate)} tenants from previous iteration")


def main():
    parser = argparse.ArgumentParser(description="Process some import phases.")

    subparsers = parser.add_subparsers(dest="command", help="Sub-command help")

    # Sub-command for import-phase-1
    parser_phase_1 = subparsers.add_parser(
        "import-phase-1", help="Phase 1 is meant to import a lot of data quickly."
    )
    parser_phase_1.set_defaults(func=import_phase_1)

    # Sub-command for import-phase-2
    parser_phase_2 = subparsers.add_parser(
        "import-phase-2",
        help="Phase 2 makes small imports across many tenants to trigger a lot of tenant activation/deactivation events",
    )
    parser_phase_2.set_defaults(func=import_phase_2)

    args = parser.parse_args()

    # If a command was provided, call the corresponding function
    if hasattr(args, "func"):
        args.func()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
