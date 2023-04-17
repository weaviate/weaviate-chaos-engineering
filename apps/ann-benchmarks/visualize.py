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

sns.set_theme()
plot = sns.relplot(
    data=df,
    markers=True,
    kind="line",
    x="recall",
    y="qps",
    hue="maxConnections",
    col="api",
    style="shards",
    # size="size",
)

plt.savefig("output.png")

print(df["recall"].idxmax)
