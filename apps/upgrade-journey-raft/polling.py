import time

from loguru import logger


def near_text_until_count(collection, expected_count: int, timeout_s: float = 30.0, **kwargs):
    """Repeat a nearText query until it returns `expected_count` objects or `timeout_s` elapses.

    Under async indexing, `wait_for_vector_indexing()` reports a shard as drained once a
    single replica's queue is empty (adapters/repos/db/index.go getShardsQueueSize goes
    through withShardOrRemote, i.e. the local replica or one remote). A query at consistency
    level ONE can be served by the other replica, whose vectors may still be queued, so the
    first nearText after an import can come back short. Polling closes that window.

    Returns the last result; the caller keeps its own assertions on it.
    """
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while True:
        attempt += 1
        result = collection.query.near_text(**kwargs)
        got = len(result.objects)
        if got == expected_count:
            if attempt > 1:
                logger.info(
                    "nearText returned {} object(s) after {} attempts", expected_count, attempt
                )
            return result
        if time.monotonic() >= deadline:
            logger.error(
                "nearText still returned {} object(s) instead of {} after {}s ({} attempts)",
                got,
                expected_count,
                timeout_s,
                attempt,
            )
            return result
        logger.warning(
            "nearText returned {} object(s), expected {}; retrying (attempt {})",
            got,
            expected_count,
            attempt,
        )
        time.sleep(0.5)
