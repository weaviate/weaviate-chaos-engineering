"""
Retry for queries that Weaviate shed under query admission control.

Since 1.37.17 a node bounds its search concurrency, and when its budget and its wait
queue are both full it refuses the arrival outright with
"query admission: node overloaded, request shed (429)" (gRPC RESOURCE_EXHAUSTED).
A shed is the server asking the client to come back in a moment, not a failed query.

weaviate-cli's benchmark counts any exception as a failed query and this test fails
the run on the first one. That was the right verdict for a node that keeps refusing
and the wrong one for the only shed seen so far: 75 refusals inside three seconds on
one pod of a two-core runner, before any pod had been restarted, in a run whose every
latency gate passed.

So a shed query is retried with exponential backoff, a bounded number of times. The
wait stays inside the latency the benchmark measures: a query that had to wait was a
slow query, and the client CSVs should say so. A query still shed after the last
retry raises the exception it always did, so a node that stays overloaded still
fails the run.
"""

import asyncio
import re
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

T = TypeVar("T")

# The limiter's own text, the status it maps to, and the gRPC code both spellings.
SHED_PATTERN = re.compile(r"request shed|\(429\)|RESOURCE_EXHAUSTED|ResourceExhausted")

DEFAULT_RETRIES = 5
DEFAULT_BASE_S = 0.1
DEFAULT_MAX_S = 2.0

QUERY_METHODS = ("hybrid", "bm25", "near_text")


def is_shed(exc: BaseException) -> bool:
    """Whether the server refused the query under admission control."""
    return bool(SHED_PATTERN.search(str(exc)))


def backoff_s(attempt: int, base_s: float = DEFAULT_BASE_S, max_s: float = DEFAULT_MAX_S) -> float:
    """Wait before retry number `attempt` (1-based): base doubling each time, capped."""
    return min(base_s * (2 ** (attempt - 1)), max_s)


class ShedStats:
    """Counts for one collection: every shed response, and how each shed query ended."""

    def __init__(self) -> None:
        self.shed = 0  # refusals received, retried or not
        self.recovered = 0  # queries shed at least once that were then served
        self.exhausted = 0  # queries still shed after the last retry

    def __repr__(self) -> str:
        return f"shed={self.shed} recovered={self.recovered} exhausted={self.exhausted}"


async def call_with_shed_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    retries: int = DEFAULT_RETRIES,
    base_s: float = DEFAULT_BASE_S,
    max_s: float = DEFAULT_MAX_S,
    stats: Optional[ShedStats] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> T:
    """
    Awaits fn(), retrying it after a shed up to `retries` times.

    Anything that is not a shed, including a timeout or a cancellation, propagates at
    once. The last shed propagates too, unchanged, so the caller's handling of it is
    the same as it was without retries.
    """
    stats = stats if stats is not None else ShedStats()
    attempt = 0
    while True:
        try:
            result = await fn()
        except Exception as exc:
            if not is_shed(exc):
                raise
            stats.shed += 1
            if attempt >= retries:
                stats.exhausted += 1
                raise
            attempt += 1
            await sleep(backoff_s(attempt, base_s, max_s))
            continue
        if attempt:
            stats.recovered += 1
        return result


def wrap_with_shed_retry(
    obj: Any,
    name: str,
    *,
    retries: int = DEFAULT_RETRIES,
    base_s: float = DEFAULT_BASE_S,
    max_s: float = DEFAULT_MAX_S,
    stats: Optional[ShedStats] = None,
) -> bool:
    """
    Replaces the async method obj.<name> with one that retries sheds.

    Idempotent, so it can be applied on every query. Returns False when obj has no
    such method, which is the case for query types the benchmark does not run.
    """
    original = getattr(obj, name, None)
    if original is None:
        return False
    if getattr(original, "_shed_retry", False):
        return True

    async def retrying(*args: Any, **kwargs: Any) -> Any:
        return await call_with_shed_retry(
            lambda: original(*args, **kwargs),
            retries=retries,
            base_s=base_s,
            max_s=max_s,
            stats=stats,
        )

    retrying._shed_retry = True  # type: ignore[attr-defined]
    setattr(obj, name, retrying)
    return True


def summarise(stats_by_collection: Dict[str, ShedStats]) -> Dict[str, str]:
    """One line per collection for the run log."""
    return {name: repr(stats) for name, stats in sorted(stats_by_collection.items())}
