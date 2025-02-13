from loguru import logger
import weaviate

host = "http://localhost:8080"


def graphql_grpc_aggregate(
    client: weaviate.WeaviateClient, class_name: str, tenant: str = ""
) -> bool:
    if len(tenant) > 0:
        collection = client.collections.get(class_name).with_tenant(tenant)
        resp = collection.aggregate.over_all(total_count=True)
        logger.info(
            "GraphQL/gRPC Aggregate {} with tenant: {} count is: {}",
            class_name,
            tenant,
            resp.total_count,
        )
    else:
        collection = client.collections.get(class_name)
        resp = collection.aggregate.over_all(total_count=True)
        logger.info("GraphQL/gRPC Aggregate {} count is: {}", class_name, resp.total_count)
    return True
