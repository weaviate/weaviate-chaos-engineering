import seaborn as sns
import matplotlib.pyplot as plt
import pandas as pd

import os, glob
import json


datapoints = []

path = "./results"
for filename in glob.glob(os.path.join(path, "*.json")):
    with open(os.path.join(os.getcwd(), filename), "r") as f:
        parsed = json.loads(f.read())
        datapoints += parsed
df = pd.DataFrame(datapoints)
df["time"] = df["run_id"].astype("int")


def heap_over_time():
    sns.set_theme()
    plot = sns.relplot(
        data=df[(df.after_restart == "true")],
        markers=True,
        kind="line",
        x="time",
        y="heap_mb",
        hue="pq",
        style="maxConnections",
        # style="shards",
        # size="size",
    )

    # get current axis
    ax = plt.gca()
    # get current xtick labels
    xticks = ax.get_xticks()
    # convert all xtick labels to selected format from ms timestamp
    ax.set_xticklabels(
        [pd.to_datetime(tm, unit="s").strftime("%Y-%m-%d\n %H:%M:%S") for tm in xticks],
        rotation=50,
    )
    plt.tight_layout()
    plt.ylim(0)
    plt.savefig("output/heap.png")


def recall_at_ef(efValues):
    sns.set_theme()
    plot = sns.relplot(
        data=df[df["ef"].isin(efValues)],
        markers=True,
        kind="line",
        x="time",
        y="recall",
        hue="pq",
        style="ef",
    )

    # get current axis
    ax = plt.gca()
    # get current xtick labels
    xticks = ax.get_xticks()
    # convert all xtick labels to selected format from ms timestamp
    ax.set_xticklabels(
        [pd.to_datetime(tm, unit="s").strftime("%Y-%m-%d\n %H:%M:%S") for tm in xticks],
        rotation=50,
    )
    plt.tight_layout()
    plt.yscale("log")
    plt.savefig(f"output/recall.png")


def qps_at_ef(efValues, pq):
    sns.set_theme()
    plot = sns.relplot(
        data=df[(df["ef"].isin(efValues)) & (df["after_restart"] == "false") & (df["pq"] == pq)],
        markers=True,
        kind="line",
        x="time",
        y="qps",
        hue="machine_type",
        style="ef",
    )

    # get current axis
    ax = plt.gca()
    # get current xtick labels
    xticks = ax.get_xticks()
    # convert all xtick labels to selected format from ms timestamp
    ax.set_xticklabels(
        [pd.to_datetime(tm, unit="s").strftime("%Y-%m-%d\n %H:%M:%S") for tm in xticks],
        rotation=50,
    )
    plt.ylim(0)
    plt.savefig(f"output/qps_{pq}.png")


heap_over_time()
recall_at_ef([16, 512])
qps_at_ef([16, 128, 512], "false")
qps_at_ef([16, 128, 512], "true")
