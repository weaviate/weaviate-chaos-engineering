import pandas as pd
import os, glob, sys
import json
import unittest


class TestResults(unittest.TestCase):
    def setUp(self):
        datapoints = []

        path = "./results"
        for filename in glob.glob(os.path.join(path, "*.json")):
            with open(os.path.join(os.getcwd(), filename), "r") as f:
                parsed = json.loads(f.read())
                datapoints += parsed
        self.df = pd.DataFrame(datapoints)

    def test_max_recall(self):
        required_recall = 0.992
        rr_env = os.getenv("REQUIRED_RECALL")
        if rr_env is not None:
            required_recall = float(rr_env)
        max_recall = self.df["recall"].max()
        self.assertTrue(
            max_recall >= required_recall,
            f"need to achieve at least {required_recall} recall, got only {max_recall}",
        )

    def test_recall_before_after(self):
        allowed_delta = 0.02
        mean_recall_before = self.df.loc[self.df["after_restart"] == "false", "recall"].mean()
        mean_recall_after = self.df.loc[self.df["after_restart"] == "true", "recall"].mean()

        delta = abs(mean_recall_before - mean_recall_after)
        self.assertTrue(
            delta < allowed_delta,
            f"allowed delta for recall before and after restart is {allowed_delta}, got before={mean_recall_before}, after={mean_recall_after}",
        )


if __name__ == "__main__":
    unittest.main()
