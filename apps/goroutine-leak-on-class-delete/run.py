import weaviate
import weaviate.classes as wvc
import uuid
import time
from loguru import logger
from datetime import timedelta
import sys

client = weaviate.connect_to_local()
client.collections.delete_all()


class StressTest:
    def __init__(self):
        self.durations = []

    def run(self, iterations: int, start_checking: int, rolling_average_count: int):
        for i in range(iterations):
            before_all = time.time()
            col = client.collections.create("BuggyBugBug")
            col.data.insert(properties={"hello": "world"}, uuid=uuid.UUID(int=1))
            col.data.replace(properties={"goodbye": "blue skies"}, uuid=uuid.UUID(int=1))
            col.data.delete_by_id(uuid.UUID(int=1))
            client.collections.delete("BuggyBugBug")
            took = time.time() - before_all
            self.durations.append(took)
            if i % 100 == 0:
                logger.info(f"[It={i:05}] Cycle took {timedelta(seconds=took)}")
                if i > start_checking:
                    self.analyze(start_checking, rolling_average_count)

    def analyze(self, lower_count, upper_count):
        mean_lower = mean(self.durations[:lower_count])
        mean_upper = mean(self.durations[(len(self.durations) - upper_count) :])

        logger.info(
            f"means: control={(mean_lower*1000):.2f}ms rolling_average={(mean_upper*1000):.2f}ms (over last {upper_count} cycles)"
        )

        ratio = abs((mean_lower - mean_upper) / mean_lower)

        if ratio > 0.25:
            logger.error(f"rolling average is too different from control: {ratio * 100}%")
            sys.exit(1)


def mean(durs: [float]) -> float:
    return sum(durs) / len(durs)


StressTest().run(iterations=15_000, start_checking=1000, rolling_average_count=250)
