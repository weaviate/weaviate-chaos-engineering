from loguru import logger
import requests

host = "http://localhost:8080"


def graphql_aggregate(class_name: str, tenant: str = "") -> bool:
    aggregate_class_part = class_name
    if len(tenant) > 0:
        aggregate_class_part = f'{class_name}(tenant:"{tenant}")'
    aggregate = "{Aggregate {" + aggregate_class_part + " {meta {count}}}}"
    res = requests.post(host + "/v1/graphql", json={"query": aggregate})
    if res.status_code == 200:
        res_body = res.json()
        logger.debug("GraphQL response: {}", res_body)
        if res_body["data"] is not None and res_body["data"]["Aggregate"] is not None:
            if res_body["data"]["Aggregate"][class_name] is not None:
                if len(res_body["data"]["Aggregate"][class_name]) == 1:
                    m = res_body["data"]["Aggregate"][class_name][0]["meta"]["count"]
                    if m is not None:
                        logger.info("GraphQL Aggregate {} count is: {}", class_name, m)
                        return True
    logger.error("GraphQL Aggregate errored, status code: {}", res.status_code)
    return False
