import weaviate
import weaviate.classes as wvc
import uuid
import time
from loguru import logger
from datetime import timedelta
from statistics import median
import sys

client = weaviate.connect_to_local()
client.collections.delete_all()


class StressTest:
    def __init__(self):
        self.durations = []
        self.breaches = 0

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
        # Medians instead of means: CPU contention on shared runners adds
        # isolated slow cycles that inflate a mean, while a leak slows every
        # cycle and moves the median too.
        median_lower = median(self.durations[:lower_count])
        median_upper = median(self.durations[(len(self.durations) - upper_count) :])

        logger.info(
            f"medians: control={(median_lower*1000):.2f}ms rolling_median={(median_upper*1000):.2f}ms (over last {upper_count} cycles)"
        )

        # Only a slowdown signals the leak: a leak makes each cycle progressively
        # slower, so the rolling median climbs above control. A faster rolling
        # median just means the control window captured cold-start warmup, so
        # keep the check one-sided rather than using abs().
        ratio = (median_upper - median_lower) / median_lower

        # A leak never recovers, so only fail once the breach persists across
        # consecutive checks; a contention burst clears within one window.
        if ratio > 0.3:
            self.breaches += 1
            logger.warning(
                f"rolling median is too much slower than control: {ratio * 100}% (breach {self.breaches}/3)"
            )
            if self.breaches >= 3:
                logger.error("slowdown persisted across 3 consecutive checks, failing")
                sys.exit(1)
        else:
            self.breaches = 0


StressTest().run(iterations=15_000, start_checking=1000, rolling_average_count=250)
