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
        if rr_env is not None and rr_env != "":
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
            f"delta {delta} for recall before and after restart beyond allowed {allowed_delta}, got before={mean_recall_before}, after={mean_recall_after}",
        )

    def test_qps_before_after(self):
        allowed_delta = 0.25
        mean_qps_before = self.df.loc[self.df["after_restart"] == "false", "qps"].mean()
        mean_qps_after = self.df.loc[self.df["after_restart"] == "true", "qps"].mean()

        min_val, max_val = min(mean_qps_before, mean_qps_after), max(
            mean_qps_before, mean_qps_after
        )
        self.assertTrue(
            min_val > max_val * (1 - allowed_delta),
            f"qps before and after restart are not within the allowed delta of {allowed_delta}, got before={mean_qps_before}, after={mean_qps_after}",
        )


if __name__ == "__main__":
    unittest.main()
