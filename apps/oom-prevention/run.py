import os
import tarfile
import requests
import weaviate
import weaviate.classes as wvc
import json
import time
from loguru import logger

delete_threshold = 12000  # ever n objects run a delete cycle


def download_file_if_not_exists(filename, url):
    """
    Checks if the specified file exists in the current working directory.
    If not, downloads the file from the given URL using loguru for logging.

    Parameters:
    - filename: The name of the file to check and download.
    - url: The URL from which to download the file if it's not present.
    """
    if not os.path.exists(filename):
        logger.info(f"{filename} not found, downloading from {url}...")
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            with open(filename, "wb") as f:
                f.write(response.content)
            logger.info(f"Downloaded {filename} successfully.")
            extract_tarball("sphere.1M.jsonl")
        else:
            logger.error(f"Failed to download {filename}. HTTP Status Code: {response.status_code}")
    else:
        logger.info(f"{filename} already exists in the current working directory.")


def extract_tarball(filepath, target_dir="."):
    """
    Extracts a gzipped tarball to the specified target directory.
    If target_dir is not specified, extracts to the current working directory.

    Parameters:
    - filepath: The path to the gzipped tarball to be extracted.
    - target_dir: The directory where the files will be extracted.
    """
    try:
        with tarfile.open(filepath, "r:gz") as tar:
            tar.extractall(path=target_dir)
        logger.info(f"Extracted {filepath} to {target_dir} successfully.")
    except Exception as e:
        logger.error(f"Failed to extract {filepath}. Error: {e}")


def import_dataset(client: weaviate.WeaviateClient, file_path: str):
    client.collections.delete("SphereOOM")
    col = client.collections.create(
        "SphereOOM",
        vector_index_config=wvc.config.Configure.VectorIndex.hnsw(cleanup_interval_seconds=30),
    )

    with open(file_path, "r") as file:
        with col.batch.dynamic() as batch:
            i = 0
            for line in file:
                obj = json.loads(line)
                batch.add_object(
                    properties={
                        "title": obj["title"],
                        "raw": obj["raw"],
                        "i": i,
                    },
                    vector=obj["vector"],
                    uuid=obj["id"],
                )

                if i % 1000 == 0:
                    err_count = batch.number_errors
                    logger.info(f"Progress {i}, control: objects={len(col)}, errors={err_count}")

                if i % delete_threshold == 0:
                    batch.flush()
                    upper_bound = (i / delete_threshold) * 1000
                    start_time = time.time()
                    del_res = col.data.delete_many(
                        where=wvc.query.Filter.by_property("i").less_or_equal(upper_bound)
                    )
                    took = time.time() - start_time
                    logger.info(
                        f"Successfully deleted {del_res.successful} out of {del_res.matches} in {took:.2f}s"
                    )

                i += 1


def main():
    download_file_if_not_exists(
        "sphere.1M.jsonl",
        "https://storage.googleapis.com/sphere-demo/sphere.1M.jsonl.tar.gz",
    )
    client = weaviate.connect_to_local()
    import_dataset(client, "sphere.1M.jsonl")


main()
