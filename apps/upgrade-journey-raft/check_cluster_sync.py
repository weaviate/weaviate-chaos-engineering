from loguru import logger
import requests, time, argparse

host = "http://localhost:8080"


def _get_nodes_count(api_key=None):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    res = requests.get(host + "/v1/nodes", headers=headers)
    if res.status_code == 200:
        res_body = res.json()
        number_of_nodes = len(res_body["nodes"])
        logger.info("Weaviate cluster consists of {} nodes", number_of_nodes)
        return number_of_nodes
    return -1


def _get_statistics(api_key=None):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    res = requests.get(host + "/v1/cluster/statistics", headers=headers)
    if res.status_code == 200:
        res_body = res.json()
        return res_body["id"], res_body["raft"]["applied_index"]


def is_in_sync(nodes: dict) -> bool:
    unique_vals = {}
    for v in nodes.values():
        unique_vals[v] = True
    return len(unique_vals) == 1


def check_cluster_sync(api_key=None):
    logger.info("started checking if cluster is in sync")
    replicas = _get_nodes_count(api_key)
    if replicas == -1:
        logger.error("can't get number of nodes in cluster")
        return

    nodes = {}
    sec, cutoff = 0, 2400
    while sec < cutoff:
        sec = sec + 1
        id, applied_index = _get_statistics(api_key)
        nodes[id] = applied_index
        logger.info("nodes len {} nodes {}", len(nodes), nodes)
        if len(nodes) == replicas and is_in_sync(nodes):
            logger.success("raft cluster is in sync")
            return

        logger.warning("raft cluster is not in sync, checking in 0.5s...")
        time.sleep(0.5)
    # cluster is not in sync, raise exception
    raise Exception("raft cluster is not in sync, stopping")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="API key for authentication", default=None)
    args = parser.parse_args()
    check_cluster_sync(args.api_key)
