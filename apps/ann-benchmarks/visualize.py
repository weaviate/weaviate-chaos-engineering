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
# df = df[df["after_restart"] == "false"]

# sns.set_theme()
# plot = sns.relplot(
#     height=7,
#     aspect=1.2,
#     data=df,
#     markers=True,
#     kind="line",
#     x="recall",
#     y="qps",
#     hue="run",
#     # style="cloud_provider",
#     # style="shards",
#     # size="size",
# )


# plt.savefig("output.png")

sns.barplot(data=df, x="run", y="import_seconds")
plt.savefig("output_import.png")

print(df["recall"].idxmax)
