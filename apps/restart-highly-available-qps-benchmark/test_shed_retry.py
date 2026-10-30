"""
Tests for the shed (429) retry around the benchmark's queries.

Run with: python3 -m unittest discover -s apps/restart-highly-available-qps-benchmark

No cluster and no real sleeping: the backoff is handed a recording sleep.
"""

import asyncio
import unittest
from typing import List

from shed_retry import (
    DEFAULT_BASE_S,
    DEFAULT_MAX_S,
    DEFAULT_RETRIES,
    ShedStats,
    backoff_s,
    call_with_shed_retry,
    is_shed,
    summarise,
    wrap_with_shed_retry,
)

# Exactly what the python client raised in the failing run, through WeaviateQueryError.
SHED_MESSAGE = (
    "Query call with protocol GRPC search failed with message object search at index "
    "rrha_mt_sync_hnsw_sq: local shard object search rrha_mt_sync_hnsw_sq_Tenant-0: "
    "query admission: node overloaded, request shed (429)."
)


class Shed(Exception):
    def __init__(self, message: str = SHED_MESSAGE) -> None:
        super().__init__(message)


class Flaky:
    """An async query that is shed `sheds` times, then serves."""

    def __init__(self, sheds: int, result: str = "served") -> None:
        self.sheds = sheds
        self.result = result
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.sheds:
            raise Shed()
        return self.result


def run(fn, **kwargs):
    """Runs the retry with a recording sleep; returns (result_or_exception, sleeps, stats)."""
    sleeps: List[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    stats = ShedStats()

    async def go():
        return await call_with_shed_retry(fn, stats=stats, sleep=fake_sleep, **kwargs)

    try:
        return asyncio.run(go()), sleeps, stats
    except BaseException as exc:  # noqa: BLE001 - the test inspects what propagated
        return exc, sleeps, stats


class TestIsShed(unittest.TestCase):
    def test_the_limiters_message_is_a_shed(self):
        self.assertTrue(is_shed(Shed()))

    def test_the_grpc_code_alone_is_a_shed(self):
        self.assertTrue(is_shed(RuntimeError("StatusCode.RESOURCE_EXHAUSTED")))
        self.assertTrue(is_shed(RuntimeError("ResourceExhausted desc = too many")))

    def test_other_errors_are_not(self):
        for text in (
            "Connection refused",
            "shard Oy6iIBxWsGWN: resolve node name \"weaviate-0\" to host",
            "status code: 422 body: local index not found",
            "deadline exceeded",
        ):
            self.assertFalse(is_shed(RuntimeError(text)), text)

    def test_a_429_elsewhere_in_a_message_does_not_match_on_its_own(self):
        """Only the limiter's parenthesised status: an object count of 429 is not a shed."""
        self.assertFalse(is_shed(RuntimeError("inserted 429 objects")))


class TestBackoff(unittest.TestCase):
    def test_doubles_from_the_base(self):
        self.assertEqual([backoff_s(n) for n in range(1, 6)], [0.1, 0.2, 0.4, 0.8, 1.6])

    def test_is_capped(self):
        self.assertEqual(backoff_s(9), DEFAULT_MAX_S)
        self.assertEqual(backoff_s(3, base_s=1.0, max_s=1.5), 1.5)

    def test_the_default_retries_fit_inside_the_per_request_timeout(self):
        """weaviate-cli wraps the whole call, retries included, in a 10s timeout; the
        backoff must leave most of that for the queries themselves."""
        total = sum(
            backoff_s(n, DEFAULT_BASE_S, DEFAULT_MAX_S) for n in range(1, DEFAULT_RETRIES + 1)
        )
        self.assertLess(total, 4.0)
        self.assertEqual(DEFAULT_RETRIES, 5)


class TestCallWithShedRetry(unittest.TestCase):
    def test_a_served_query_is_returned_untouched(self):
        fn = Flaky(sheds=0)
        result, sleeps, stats = run(fn)
        self.assertEqual(result, "served")
        self.assertEqual(sleeps, [])
        self.assertEqual((stats.shed, stats.recovered, stats.exhausted), (0, 0, 0))
        self.assertEqual(fn.calls, 1)

    def test_a_shed_query_is_retried_with_exponential_backoff(self):
        fn = Flaky(sheds=3)
        result, sleeps, stats = run(fn)
        self.assertEqual(result, "served")
        self.assertEqual(sleeps, [0.1, 0.2, 0.4])
        self.assertEqual(fn.calls, 4)
        self.assertEqual((stats.shed, stats.recovered, stats.exhausted), (3, 1, 0))

    def test_the_fifth_retry_is_the_last(self):
        """Shed on the first call and all five retries: the sixth shed propagates."""
        fn = Flaky(sheds=6)
        result, sleeps, stats = run(fn)
        self.assertIsInstance(result, Shed)
        self.assertEqual(sleeps, [0.1, 0.2, 0.4, 0.8, 1.6])
        self.assertEqual(fn.calls, 6)
        self.assertEqual((stats.shed, stats.recovered, stats.exhausted), (6, 0, 1))

    def test_served_on_the_last_retry_counts_as_recovered(self):
        fn = Flaky(sheds=5)
        result, sleeps, stats = run(fn)
        self.assertEqual(result, "served")
        self.assertEqual(len(sleeps), 5)
        self.assertEqual((stats.shed, stats.recovered, stats.exhausted), (5, 1, 0))

    def test_the_propagated_error_is_the_original(self):
        """weaviate-cli prints the exception text, and the log should still carry
        the limiter's own words rather than a wrapper's."""
        result, _, _ = run(Flaky(sheds=10))
        self.assertIn("request shed (429)", str(result))

    def test_a_non_shed_error_is_not_retried(self):
        async def refused() -> str:
            raise ConnectionError("Connection refused")

        result, sleeps, stats = run(refused)
        self.assertIsInstance(result, ConnectionError)
        self.assertEqual(sleeps, [])
        self.assertEqual(stats.shed, 0)

    def test_a_timeout_is_not_retried(self):
        """Timeouts have their own gate with its own tolerance."""

        async def slow() -> str:
            raise asyncio.TimeoutError()

        result, sleeps, _ = run(slow)
        self.assertIsInstance(result, asyncio.TimeoutError)
        self.assertEqual(sleeps, [])

    def test_cancellation_passes_straight_through(self):
        async def cancelled() -> str:
            raise asyncio.CancelledError()

        result, sleeps, _ = run(cancelled)
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertEqual(sleeps, [])

    def test_zero_retries_means_the_first_shed_propagates(self):
        result, sleeps, stats = run(Flaky(sheds=1), retries=0)
        self.assertIsInstance(result, Shed)
        self.assertEqual(sleeps, [])
        self.assertEqual((stats.shed, stats.exhausted), (1, 1))

    def test_retries_and_backoff_are_tunable(self):
        result, sleeps, _ = run(Flaky(sheds=2), retries=2, base_s=0.5, max_s=0.75)
        self.assertEqual(result, "served")
        self.assertEqual(sleeps, [0.5, 0.75])

    def test_stats_accumulate_across_queries(self):
        stats = ShedStats()

        async def fake_sleep(s: float) -> None:
            pass

        async def go():
            for sheds in (0, 2, 7, 1):
                try:
                    await call_with_shed_retry(Flaky(sheds), stats=stats, sleep=fake_sleep)
                except Shed:
                    pass

        asyncio.run(go())
        self.assertEqual((stats.shed, stats.recovered, stats.exhausted), (9, 2, 1))
        self.assertEqual(repr(stats), "shed=9 recovered=2 exhausted=1")


class Query:
    """Stands in for the client's collection.query, with the three benchmark methods."""

    def __init__(self, sheds: int) -> None:
        self.hybrid_calls = 0
        self.sheds = sheds

    async def hybrid(self, query: str, limit: int = 10) -> str:
        self.hybrid_calls += 1
        if self.hybrid_calls <= self.sheds:
            raise Shed()
        return f"{query}:{limit}"

    async def bm25(self, query: str) -> str:
        return query


class TestWrapWithShedRetry(unittest.TestCase):
    def test_the_wrapped_method_retries_and_keeps_its_arguments(self):
        q = Query(sheds=2)
        stats = ShedStats()
        self.assertTrue(wrap_with_shed_retry(q, "hybrid", stats=stats))

        async def go():
            # The real wrapper sleeps; keep the test quick with a small base.
            return await q.hybrid("test", limit=3)

        wrap_with_shed_retry(q, "hybrid", stats=stats, base_s=0.001)
        self.assertEqual(asyncio.run(go()), "test:3")
        self.assertEqual(q.hybrid_calls, 3)
        self.assertEqual(stats.shed, 2)

    def test_wrapping_twice_does_not_stack(self):
        """Applied on every query, so a second application must find the first."""
        q = Query(sheds=0)
        wrap_with_shed_retry(q, "hybrid")
        first = q.hybrid
        wrap_with_shed_retry(q, "hybrid")
        self.assertIs(q.hybrid, first)

    def test_a_query_type_the_client_lacks_is_skipped(self):
        q = Query(sheds=0)
        self.assertFalse(wrap_with_shed_retry(q, "near_text"))
        self.assertTrue(wrap_with_shed_retry(q, "bm25"))

    def test_the_summary_is_one_line_per_collection_sorted(self):
        a, b = ShedStats(), ShedStats()
        b.shed = 3
        self.assertEqual(
            summarise({"zed": a, "alpha": b}),
            {"alpha": "shed=3 recovered=0 exhausted=0", "zed": "shed=0 recovered=0 exhausted=0"},
        )


if __name__ == "__main__":
    unittest.main()
