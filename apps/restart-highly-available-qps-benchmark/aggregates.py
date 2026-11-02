"""
Aggregates during a restart: what the test can and cannot blame on Weaviate.

The benchmark's count aggregates go over REST GraphQL through a kubectl-relay
port-forward to the weaviate Service. The relay dials the Service for every new
connection and kube-proxy picks the endpoint with no health awareness, so for about a
second after a pod is terminated the pick can be the pod that just went away. A SYN to
a vanished pod IP is dropped, not refused, so that dial hangs for the relay's 10s
connect timeout and the request never reaches Weaviate. The rolling-restart job once
scored exactly that as a 10004ms aggregate and failed the run on it.

A verdict from Weaviate always arrives as an HTTP response. A relay dial failure never
does: the relay closes the local connection without sending a byte, which requests
reports as a ConnectionError. That is the line drawn here:

  served        HTTP 200 without GraphQL errors; its latency is a sample
  server_error  any other HTTP response, or a read that timed out; a failed sample
  no_response   no HTTP response at all; not Weaviate's verdict, so it is retried

A no-response attempt is retried with exponential backoff, and only the attempt that
got a response is a latency sample. A pod killed mid-request looks the same as a relay
dial failure, and for an idempotent count the same retry is right for both. Running out
of retries is a real outage, and the caller fails the run on it.
"""

import re
import time
from typing import Any, Callable, Dict, Optional, Tuple

from shed_retry import backoff_s

SERVED = "served"
SERVER_ERROR = "server_error"
NO_RESPONSE = "no_response"
OUTCOMES = (SERVED, SERVER_ERROR, NO_RESPONSE)

NO_RESPONSE_RETRIES = 3
NO_RESPONSE_BASE_S = 0.25
NO_RESPONSE_MAX_S = 2.0

# One line per upstream dial the relay could not complete, from its own log.
RELAY_DIAL_FAILURE = re.compile(r"Fail to connect reqID=\S+ error=\"([^\"]*)\"")


def classify_exception(exc: BaseException) -> str:
    """
    What a raised request means.

    requests reports a connection that closed or reset before any HTTP bytes as a
    ConnectionError (so does the standard library). A read timeout is the opposite
    case: the server took the request and never answered, which is a hang on
    Weaviate's side, so it is a failed sample rather than a retry. Matched by class
    name along the MRO so the module needs neither requests nor urllib3.
    """
    names = {cls.__name__ for cls in type(exc).__mro__}
    if "ReadTimeout" in names:
        return SERVER_ERROR
    if "ConnectionError" in names:
        return NO_RESPONSE
    return SERVER_ERROR


def classify_response(status_code: int, body: Any) -> str:
    """A GraphQL response: 200 with no errors key is served; anything else is Weaviate's."""
    if status_code == 200 and isinstance(body, dict) and "errors" not in body:
        return SERVED
    return SERVER_ERROR


class AggregateStats:
    """Counts kept by the aggregate loop. Only answered attempts are latency samples."""

    def __init__(self) -> None:
        self.served = 0
        self.server_errors = 0
        self.slow = 0  # answered attempts over the slow limit
        self.max_ms = 0.0  # over answered attempts
        self.no_response = 0  # attempts that got no HTTP response, retried or not
        self.recovered = 0  # aggregates answered after at least one no-response attempt
        self.exhausted = 0  # aggregates that got no response on every attempt

    @property
    def samples(self) -> int:
        return self.served + self.server_errors

    def record(self, elapsed_ms: float, outcome: str, slow_ms: float) -> None:
        if outcome == NO_RESPONSE:
            self.no_response += 1
            return
        if outcome == SERVED:
            self.served += 1
        else:
            self.server_errors += 1
        self.max_ms = max(self.max_ms, elapsed_ms)
        if elapsed_ms > slow_ms:
            self.slow += 1

    def __repr__(self) -> str:
        return (
            f"served={self.served} server_errors={self.server_errors} slow={self.slow} "
            f"max_ms={self.max_ms:.0f} no_response={self.no_response} "
            f"recovered={self.recovered} exhausted={self.exhausted}"
        )


def aggregate_with_retry(
    once: Callable[[], Tuple[float, str]],
    stats: AggregateStats,
    *,
    slow_ms: float,
    retries: int = NO_RESPONSE_RETRIES,
    base_s: float = NO_RESPONSE_BASE_S,
    max_s: float = NO_RESPONSE_MAX_S,
    sleep: Callable[[float], Any] = time.sleep,
) -> Tuple[float, str, int]:
    """
    Runs one aggregate, retrying attempts that got no response.

    Returns (elapsed_ms, outcome, attempts) for the attempt that settled it: the first
    answered one, or the last unanswered one when the retries ran out. Every attempt
    is recorded in stats, so a dial that stalled still shows in the no-response count
    even when the retry was answered at once.
    """
    attempt = 0
    while True:
        elapsed_ms, outcome = once()
        stats.record(elapsed_ms, outcome, slow_ms)
        if outcome != NO_RESPONSE:
            if attempt:
                stats.recovered += 1
            return elapsed_ms, outcome, attempt + 1
        if attempt >= retries:
            stats.exhausted += 1
            return elapsed_ms, outcome, attempt + 1
        attempt += 1
        sleep(backoff_s(attempt, base_s, max_s))


def relay_dial_failures(relay_log: str) -> Dict[str, int]:
    """Upstream dial failures the relay logged, counted by its error text."""
    counts: Dict[str, int] = {}
    for error in RELAY_DIAL_FAILURE.findall(relay_log):
        counts[error] = counts.get(error, 0) + 1
    return counts


def read_relay_log(path: str) -> Optional[str]:
    """The relay's log when the job captured one, else None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None
