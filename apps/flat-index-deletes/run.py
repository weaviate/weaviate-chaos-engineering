import weaviate
import weaviate.classes as wvc
import numpy as np
from loguru import logger
import time
from datetime import timedelta
import matplotlib.pyplot as plt
import sys


chunks_per_pdf = 4000
chunk_limit = 12000
dimensions = 256
total_pdfs = 500
queries_per_pdf = 100

pdf_counter = 0


def do():
    client = weaviate.connect_to_local()
    client.collections.delete_all()

    old_client = weaviate.Client(url="http://localhost:8080")
    old_client.schema.create_class(
        {
            "class": "MyPDFs",
            "vectorIndexType": "flat",
            "properties": [
                {
                    "name": "pdf_id",
                    "dataType": ["int"],
                },
                {
                    "name": "chunk_id",
                    "dataType": ["int"],
                },
            ],
        }
    )

    col = client.collections.get("MyPDFs")

    main_loop(col, old_client)


def main_loop(col: weaviate.collections.collections._Collection, old_client: weaviate.Client):
    import_durations = []
    query_latencies = []

    for i in range(total_pdfs):
        while len(col) >= chunk_limit:
            delete_random_pdf(col, old_client)

        import_durations.append(import_one_pdf(col))
        query_latencies.append(query_random_pdf(col, old_client))

    visualize_data(import_durations, query_latencies)
    validate_nothing_degraded(import_durations, query_latencies)


def get_random_pdf_id(
    col: weaviate.collections.collections._Collection, old_client: weaviate.Client
) -> int:
    # res = col.query.near_vector(near_vector=np.random.rand(1, dimensions).tolist()[0], limit=1)
    # pdf_id = res.objects[0].properties["pdf_id"]

    res = (
        old_client.query.get(class_name="MyPDFs", properties=["pdf_id"])
        .with_near_vector({"vector": np.random.rand(1, dimensions).tolist()[0]})
        .with_limit(1)
        .do()
    )
    pdf_id = res["data"]["Get"]["MyPDFs"][0]["pdf_id"]
    return int(pdf_id)


def delete_random_pdf(
    col: weaviate.collections.collections._Collection, old_client: weaviate.Client
):
    pdf_id = get_random_pdf_id(col, old_client)

    before = time.time()
    col.data.delete_many(where=wvc.Filter("pdf_id").equal(pdf_id))
    took = time.time() - before
    logger.info(
        f"Deleted old PDF (pdf_id={pdf_id}) to make room for a new one in {timedelta(seconds=took)}"
    )


def query_random_pdf(
    col: weaviate.collections.collections._Collection, old_client: weaviate.Client
) -> float:
    pdf_id = get_random_pdf_id(col, old_client)
    query_time = 0
    for i in range(queries_per_pdf):
        before = time.time()
        res = (
            old_client.query.get(class_name="MyPDFs", properties=["_additional {id}"])
            .with_near_vector({"vector": np.random.rand(1, dimensions).tolist()[0]})
            .with_where({"path": ["pdf_id"], "valueInt": pdf_id, "operator": "Equal"})
            .with_limit(10)
            .do()
        )
        query_time += time.time() - before
        assert len(res["data"]["Get"]["MyPDFs"]) == 10

    query_time = query_time / queries_per_pdf

    logger.info(f"query pdf_id={pdf_id} latency: mean={int(query_time * 1000)}ms")
    return query_time


def import_one_pdf(col: weaviate.collections.collections._Collection) -> float:
    global pdf_counter

    start = time.time()
    chunks = []
    for i in range(chunks_per_pdf):
        chunks.append(
            wvc.DataObject(
                properties={
                    "pdf_id": pdf_counter,
                    "chunk_id": i,
                },
                vector=np.random.rand(1, dimensions).tolist()[0],
            )
        )

        if (i + 1) % 100 == 0:
            col.data.insert_many(chunks)
            chunks = []

    took = time.time() - start
    logger.info(
        f"Imported PDF {pdf_counter} ({chunks_per_pdf} chunks) in {timedelta(seconds=took)}"
    )
    pdf_counter += 1
    return took


def visualize_data(import_durations: [float], query_latencies: [float]):
    # Convert the time in seconds to milliseconds
    import_durations_ms = [dur * 1000 for dur in import_durations]
    query_latencies_ms = [lat * 1000 for lat in query_latencies]

    # Create a time axis (assumes data points are evenly spaced)
    time_axis = [i for i in range(len(import_durations))]

    # Create the first chart for import durations
    plt.figure(figsize=(8, 4), dpi=300)
    plt.plot(time_axis, import_durations_ms, label="Import Durations (ms)", color="blue")
    plt.xlabel("Time (Data Point Index)")
    plt.ylabel("Duration (ms)")
    plt.title("Import Durations Over Time")
    plt.legend()
    plt.grid(True)
    plt.ylim(0, max(import_durations_ms) * 1.2)

    # Save the first chart to a file (e.g., import_durations_chart.png)
    plt.savefig("import_durations_chart.png")

    # Clear the current figure
    plt.clf()

    # Create the second chart for query latencies
    plt.figure(figsize=(8, 4), dpi=300)
    plt.plot(time_axis, query_latencies_ms, label="Query Latencies (ms)", color="green")
    plt.xlabel("Time (Data Point Index)")
    plt.ylabel("Latency (ms)")
    plt.title("Query Latencies Over Time")
    plt.legend()
    plt.grid(True)
    plt.ylim(0, max(query_latencies_ms) * 1.2)

    # Save the second chart to a file (e.g., query_latencies_chart.png)
    plt.savefig("query_latencies_chart.png")


def validate_nothing_degraded(import_durations: [float], query_durations: [float]):
    fail = False

    # compare first 10% of durations to last 10% of durations. Make sure they
    # are no more than a pre-defined threshold apart
    cutoff_10 = int(len(import_durations) * 0.1)
    cutoff_90 = int(len(import_durations) * 0.9)

    beginning_mean = mean(import_durations[0:cutoff_10])
    end_mean = mean(import_durations[cutoff_90:])

    logger.info(
        f"Import duration over first 10% of requests was {(beginning_mean * 1000):.2f}ms, last 10% was {(end_mean * 1000):.2f}ms"
    )

    if end_mean < beginning_mean:
        logger.info("end durations are lower than beginning durations - PASS")
    else:
        diff = abs((end_mean - beginning_mean) / beginning_mean)
        if diff < 0.15:
            logger.info(
                "end durations are within 15% of beginning, which we consider the margin of error - PASS"
            )
        else:
            logger.error(f"end durations are {diff * 100}% higher than beginning - FAIL")
            fail = True

    # repeat for queries
    cutoff_10 = int(len(query_durations) * 0.1)
    cutoff_90 = int(len(query_durations) * 0.9)

    beginning_mean = mean(query_durations[0:cutoff_10])
    end_mean = mean(query_durations[cutoff_90:])

    logger.info(
        f"Query latency over first 10% of requests was {(beginning_mean * 1000):.2f}ms, last 10% was {(end_mean * 1000):.2f}ms"
    )

    if end_mean < beginning_mean:
        logger.info("end latencies are lower than beginning latencies - PASS")
    else:
        diff = abs((end_mean - beginning_mean) / beginning_mean)
        if diff < 0.15:
            logger.info(
                "end latencies are within 15% of beginning, which we consider the margin of error - PASS"
            )
        else:
            logger.error(f"end latencies are {diff * 100}% higher than beginning - FAIL")
            fail = True
    if fail:
        sys.exit(1)


def mean(in_list: [float]) -> float:
    return sum(in_list) / len(in_list)


do()
