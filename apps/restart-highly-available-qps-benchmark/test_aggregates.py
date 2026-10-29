"""
Tests for the aggregate loop's outcome classification and no-response retry.

Run with: python3 -m unittest discover -s apps/restart-highly-available-qps-benchmark

No cluster, no relay and no real sleeping. The exception classes below mirror the
names requests and the standard library raise, which is all the classifier reads.
"""

import unittest
from typing import List, Tuple

from aggregates import (
    NO_RESPONSE,
    NO_RESPONSE_BASE_S,
    NO_RESPONSE_MAX_S,
    NO_RESPONSE_RETRIES,
    SERVED,
    SERVER_ERROR,
    AggregateStats,
    aggregate_with_retry,
    classify_exception,
    classify_response,
    relay_dial_failures,
)
from shed_retry import backoff_s


# requests.exceptions, by name and inheritance.
class RequestException(IOError):
    pass


class ConnectionError(RequestException):  # noqa: A001 - mirrors requests' name on purpose
    pass


class Timeout(RequestException):
    pass


class ConnectTimeout(ConnectionError, Timeout):
    pass


class ReadTimeout(Timeout):
    pass


class HTTPError(RequestException):
    pass


class TestClassifyException(unittest.TestCase):
    def test_a_connection_closed_without_a_response_is_no_response(self):
        """The relay closes the local socket without a byte when its upstream dial
        fails; requests raises ConnectionError for that and for a reset."""
        self.assertEqual(classify_exception(ConnectionError("Connection aborted.")), NO_RESPONSE)
        self.assertEqual(classify_exception(ConnectionResetError()), NO_RESPONSE)
        self.assertEqual(classify_exception(ConnectionRefusedError()), NO_RESPONSE)

    def test_a_connect_timeout_is_no_response(self):
        self.assertEqual(classify_exception(ConnectTimeout()), NO_RESPONSE)

    def test_a_read_timeout_is_weaviates(self):
        """The server took the request and never answered: that is the hang the slow
        gate exists for, not something to retry away."""
        self.assertEqual(classify_exception(ReadTimeout()), SERVER_ERROR)

    def test_anything_else_is_a_failed_sample(self):
        self.assertEqual(classify_exception(HTTPError()), SERVER_ERROR)
        self.assertEqual(classify_exception(ValueError("not json")), SERVER_ERROR)
        self.assertEqual(classify_exception(RuntimeError("boom")), SERVER_ERROR)


class TestClassifyResponse(unittest.TestCase):
    def test_200_without_errors_is_served(self):
        body = {"data": {"Aggregate": {"Rrha": [{"meta": {"count": 15000}}]}}}
        self.assertEqual(classify_response(200, body), SERVED)

    def test_graphql_errors_are_a_server_error_even_on_200(self):
        body = {"data": None, "errors": [{"message": "shard x: no nodes reported object count"}]}
        self.assertEqual(classify_response(200, body), SERVER_ERROR)

    def test_non_200_is_a_server_error(self):
        self.assertEqual(classify_response(503, {"error": "not ready"}), SERVER_ERROR)
        self.assertEqual(classify_response(500, None), SERVER_ERROR)

    def test_a_200_that_is_not_json_is_a_server_error(self):
        self.assertEqual(classify_response(200, None), SERVER_ERROR)
        self.assertEqual(classify_response(200, "<html>"), SERVER_ERROR)


def run(
    attempts: List[Tuple[float, str]], slow_ms: float = 5000.0, **kwargs
) -> Tuple[Tuple[float, str, int], List[float], AggregateStats, int]:
    """Feeds scripted attempts to the retry; returns (result, sleeps, stats, calls)."""
    sleeps: List[float] = []
    calls = {"n": 0}
    stats = AggregateStats()

    def once() -> Tuple[float, str]:
        i = calls["n"]
        calls["n"] += 1
        return attempts[min(i, len(attempts) - 1)]

    result = aggregate_with_retry(once, stats, slow_ms=slow_ms, sleep=sleeps.append, **kwargs)
    return result, sleeps, stats, calls["n"]


class TestAggregateWithRetry(unittest.TestCase):
    def test_an_answered_aggregate_is_one_sample_and_no_sleep(self):
        result, sleeps, stats, calls = run([(42.0, SERVED)])
        self.assertEqual(result, (42.0, SERVED, 1))
        self.assertEqual(sleeps, [])
        self.assertEqual(calls, 1)
        self.assertEqual((stats.served, stats.samples, stats.no_response), (1, 1, 0))
        self.assertEqual(stats.max_ms, 42.0)

    def test_the_relay_stall_is_retried_and_only_the_answer_is_timed(self):
        """The failing run: a 10004ms dial timeout, then an answer in 40ms."""
        result, sleeps, stats, _ = run([(10004.0, NO_RESPONSE), (40.0, SERVED)])
        self.assertEqual(result, (40.0, SERVED, 2))
        self.assertEqual(sleeps, [0.25])
        self.assertEqual(stats.slow, 0, "the stalled dial is not a latency sample")
        self.assertEqual(stats.max_ms, 40.0)
        self.assertEqual((stats.no_response, stats.recovered, stats.exhausted), (1, 1, 0))

    def test_backoff_doubles_between_unanswered_attempts(self):
        attempts = [(1.0, NO_RESPONSE)] * 3 + [(50.0, SERVED)]
        result, sleeps, stats, calls = run(attempts)
        self.assertEqual(result[1:], (SERVED, 4))
        self.assertEqual(sleeps, [0.25, 0.5, 1.0])
        self.assertEqual(calls, 4)
        self.assertEqual((stats.no_response, stats.recovered), (3, 1))

    def test_running_out_of_retries_is_exhausted_not_a_sample(self):
        result, sleeps, stats, calls = run([(10004.0, NO_RESPONSE)])
        self.assertEqual(result, (10004.0, NO_RESPONSE, NO_RESPONSE_RETRIES + 1))
        self.assertEqual(len(sleeps), NO_RESPONSE_RETRIES)
        self.assertEqual(calls, NO_RESPONSE_RETRIES + 1)
        self.assertEqual(stats.samples, 0)
        self.assertEqual(stats.slow, 0)
        self.assertEqual((stats.no_response, stats.exhausted), (NO_RESPONSE_RETRIES + 1, 1))

    def test_a_server_error_is_a_failed_sample_and_is_not_retried(self):
        result, sleeps, stats, calls = run([(30.0, SERVER_ERROR), (40.0, SERVED)])
        self.assertEqual(result, (30.0, SERVER_ERROR, 1))
        self.assertEqual(sleeps, [])
        self.assertEqual(calls, 1)
        self.assertEqual((stats.server_errors, stats.served), (1, 0))

    def test_a_slow_answer_still_counts_as_slow(self):
        """A read timeout is a server error with the full wait on it, and it must
        reach the slow gate exactly as it did before."""
        _, _, stats, _ = run([(90000.0, SERVER_ERROR)])
        self.assertEqual((stats.slow, stats.server_errors, stats.max_ms), (1, 1, 90000.0))

    def test_a_slow_answer_after_a_stall_is_slow_too(self):
        _, _, stats, _ = run([(10004.0, NO_RESPONSE), (6000.0, SERVED)])
        self.assertEqual((stats.slow, stats.recovered), (1, 1))

    def test_retries_and_backoff_are_tunable(self):
        result, sleeps, stats, _ = run([(1.0, NO_RESPONSE)], retries=1, base_s=0.1, max_s=0.1)
        self.assertEqual(result[2], 2)
        self.assertEqual(sleeps, [0.1])
        self.assertEqual(stats.exhausted, 1)

    def test_zero_retries_restores_the_old_behaviour(self):
        result, sleeps, stats, _ = run([(10004.0, NO_RESPONSE)], retries=0)
        self.assertEqual(result, (10004.0, NO_RESPONSE, 1))
        self.assertEqual(sleeps, [])
        self.assertEqual(stats.exhausted, 1)

    def test_the_default_backoff_outlasts_endpoint_propagation(self):
        """kube-proxy takes up to about a second to stop routing to a terminated pod;
        the retries must span more than that, without turning a dead cluster into a
        long wait."""
        total = sum(
            backoff_s(n, NO_RESPONSE_BASE_S, NO_RESPONSE_MAX_S)
            for n in range(1, NO_RESPONSE_RETRIES + 1)
        )
        self.assertGreater(total, 1.0)
        self.assertLess(total, 5.0)

    def test_stats_accumulate_across_the_loop(self):
        stats = AggregateStats()
        script = [
            [(40.0, SERVED)],
            [(10004.0, NO_RESPONSE), (40.0, SERVED)],
            [(1.0, NO_RESPONSE)] * 4,
            [(30.0, SERVER_ERROR)],
        ]
        for attempts in script:
            it = iter(attempts)
            aggregate_with_retry(lambda: next(it), stats, slow_ms=5000.0, sleep=lambda s: None)
        self.assertEqual(
            repr(stats),
            "served=2 server_errors=1 slow=0 max_ms=40 no_response=5 recovered=1 exhausted=1",
        )


RELAY_LOG = """\
2026/10/29 20:11:16 INFO Handling tcp connection reqID=Un872 dstAddr=10.96.68.16:80 localAddr=...
2026/10/29 20:11:16 ERROR Fail to connect reqID=Un872 error="Unknown error"
2026/10/29 20:11:45 INFO Handling tcp connection reqID=pHxCv dstAddr=10.96.68.16:80 localAddr=...
2026/10/29 20:11:55 ERROR Fail to connect reqID=pHxCv error="Connect timeout"
2026/10/29 20:11:58 ERROR Fail to connect reqID=ykT8U error="Unknown error"
2026/10/29 20:11:58 (0xc0001cabb0) Data frame received for 227
"""


class TestRelayLog(unittest.TestCase):
    def test_dial_failures_are_counted_by_error(self):
        """The lines the failing run's artifact held."""
        self.assertEqual(
            relay_dial_failures(RELAY_LOG), {"Unknown error": 2, "Connect timeout": 1}
        )

    def test_a_quiet_log_has_none(self):
        self.assertEqual(relay_dial_failures("2026/10/29 20:11:16 INFO Handling tcp\n"), {})
        self.assertEqual(relay_dial_failures(""), {})


if __name__ == "__main__":
    unittest.main()
