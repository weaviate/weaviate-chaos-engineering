"""Authoritative in-memory model of CL.ALL-acked state + the retry-until-ack primitive.

The model only ever commits state that Weaviate acked at ConsistencyLevel.ALL, so a torn write
that lands mid-move is never recorded as expected until every replica agrees. Deterministic
(id, payload) pairs make every retry byte-identical, so a partially-applied write self-heals.
"""

import asyncio
import enum
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger


class AckBudgetExceeded(Exception):
    """A CL.ALL op could not be acked within RETRY_BUDGET; recorded as a FINDING."""


class Outcome(enum.Enum):
    ACK = "ack"
    RETRY = "retry"


# Substrings that mark a transient, safely-retryable failure of a CL.ALL op. A write briefly
# hitting a shard whose replica is mid-move surfaces the busy-replica 500; retrying the
# idempotent op heals it once the move settles.
_TRANSIENT_SUBSTRINGS = (
    "replica is already being replicated",
    "context deadline exceeded",
    "context canceled",
    "deadline",
    "timeout",
    "timed out",
    "connection",
    "unavailable",
    "not enough",
    "could not reach",
    "not reached",
    "broken pipe",
    "reset by peer",
    "temporarily",
    # A CL.ALL write racing the tenants worker's concurrent deactivate can hit 422 "tenant not
    # active"; auto_tenant_activation reactivates it, so the idempotent retry succeeds. A tenant
    # that is permanently un-activatable still FINDINGs via AckBudgetExceeded once the budget runs.
    "not active",
)


def exc_status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def is_transient_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in _TRANSIENT_SUBSTRINGS)


def has_substring(exc: BaseException, needle: str) -> bool:
    return needle.lower() in str(exc).lower()


@dataclass
class Counters:
    seeded: int = 0
    inserts: int = 0
    updates: int = 0
    deletes: int = 0
    moves_started: int = 0
    moves_completed: int = 0
    move_conflict_rejected: int = 0
    move_422: int = 0
    tenant_move_conflict_rejected: int = 0
    activations: int = 0
    deactivations: int = 0

    def summary(self) -> str:
        return (
            f"seeded={self.seeded} inserts={self.inserts} updates={self.updates} "
            f"deletes={self.deletes} moves_started={self.moves_started} "
            f"moves_completed={self.moves_completed} "
            f"move_conflict_rejected={self.move_conflict_rejected} move_422={self.move_422} "
            f"tenant_move_conflict_rejected={self.tenant_move_conflict_rejected} "
            f"activations={self.activations} deactivations={self.deactivations}"
        )


class Model:
    def __init__(self, tenant_names: list[str]) -> None:
        # tenant -> {object_uuid(str) -> payload(dict)} of CL.ALL-acked objects only.
        self.objects: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in tenant_names}
        # Held across pick -> write -> commit so a tenant's model and its writes never interleave.
        self.locks: dict[str, asyncio.Lock] = {t: asyncio.Lock() for t in tenant_names}
        self.tenant_status: dict[str, str] = {t: "ACTIVE" for t in tenant_names}
        # shard(tenant) -> op_id (or "PENDING" during await-free reservation). <=1 MOVE per shard.
        self.inflight: dict[str, str] = {}
        self.moves_lock = asyncio.Lock()
        self.counters = Counters()
        self._next_idx: dict[str, int] = {t: 0 for t in tenant_names}

    def reserve_idx(self, tenant: str) -> int:
        idx = self._next_idx[tenant]
        self._next_idx[tenant] = idx + 1
        return idx

    def random_existing_id(self, tenant: str) -> str | None:
        ids = self.objects.get(tenant)
        if not ids:
            return None
        return random.choice(list(ids.keys()))


async def retry_until_ack(
    op_fn: Callable[[], Awaitable[Any]],
    classify: Callable[[Any | None, BaseException | None], Outcome],
    *,
    budget: int,
    backoff_ms: int,
    label: str,
) -> None:
    """Invoke the idempotent op_fn until `classify` returns ACK or the budget is exhausted.

    classify inspects (result, exc) and returns ACK / RETRY, or re-raises for a permanent
    non-transient failure (propagated as a FINDING). Backoff is exponential with jitter.
    """
    for attempt in range(budget):
        result: Any | None = None
        exc: BaseException | None = None
        try:
            result = await op_fn()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # classify decides retry vs permanent; it may re-raise
            exc = e
        if classify(result, exc) is Outcome.ACK:
            return
        delay = (backoff_ms / 1000.0) * (2 ** min(attempt, 5))
        delay += random.uniform(0, backoff_ms / 1000.0)
        await asyncio.sleep(delay)
    raise AckBudgetExceeded(f"{label}: no ack within {budget} attempts")


@dataclass
class Findings:
    """Collects FINDING messages so the run reports every invariant breach, not just the first."""

    messages: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        logger.error("FINDING: {msg}", msg=msg)
        self.messages.append(msg)

    @property
    def ok(self) -> bool:
        return not self.messages
