"""Continuous neighbour-namespace write load, model-tracked, pausable and size-governed.

One asyncio task per (neighbour namespace, collection). Each task is the sole writer of its
ExpectedSet, so the model needs no lock; a second writer would be a design error.
"""

import asyncio
import random
import uuid as uuidlib
from typing import Any

import httpx
from loguru import logger
from weaviate.exceptions import UnexpectedStatusCodeError, WeaviateBaseError

import wvclient
from config import Config
from model import ExpectedObject, ExpectedSet
from restapi import poll
from seed import SeededCollection, SeededNamespace, SourceState, object_properties


class LoadError(Exception):
    """The load could not keep the model authoritative. Neighbour integrity would be vacuous."""


class _Worker:
    def __init__(self, namespace: SeededNamespace, seeded: SeededCollection, collection: Any):
        self.key = f"{namespace.name}:{seeded.short_name}"
        self.seeded = seeded
        self.collection = collection
        self.parked = True


class NeighbourLoad:
    def __init__(self, cfg: Config, state: SourceState):
        self._cfg = cfg
        self._state = state
        self._running = asyncio.Event()
        self._stopping = False
        self._stopped = False
        self._workers: list[_Worker] = []
        self._tasks: list[asyncio.Task] = []
        self._clients: list[Any] = []
        self._failure: BaseException | None = None

    # --- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        for namespace in self._state.neighbours:
            client = wvclient.build_client(self._cfg.source, namespace.admin_user.api_key)
            await client.connect()
            self._clients.append(client)
            for seeded in namespace.collections:
                self._workers.append(
                    _Worker(namespace, seeded, client.collections.use(seeded.short_name))
                )
        self._running.set()
        for worker in self._workers:
            self._tasks.append(asyncio.create_task(self._run(worker), name=f"load:{worker.key}"))
        logger.info(f"neighbour load started on {len(self._workers)} collections")

    async def pause(self) -> None:
        """Quiesce barrier: return only once every task is parked between operations."""
        self._running.clear()
        await poll(
            self._all_parked,
            deadline_s=self._cfg.load_pause_timeout_s,
            interval_s=0.2,
            describe="neighbour load to quiesce",
        )
        self.raise_if_failed()

    async def resume(self) -> None:
        if not self._stopping:
            self._running.set()

    def paused(self) -> "_PausedLoad":
        return _PausedLoad(self)

    def is_quiescent(self) -> bool:
        """True while paused and after stopping. Every model comparison checks this first."""
        if self._stopped:
            return True
        if self._running.is_set():
            return False
        return all(w.parked or t.done() for w, t in zip(self._workers, self._tasks))

    async def stop_and_drain(self) -> None:
        if self._stopped:
            return
        self._stopping = True
        # Stop wins over pause everywhere: releasing the waiters before awaiting the tasks means a
        # task parked in a pause cannot outlive a stop, and a raise inside a paused block cannot
        # deadlock this call.
        self._running.set()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._stopped = True
        for worker in self._workers:
            try:
                actual = await wvclient.read_all(worker.collection)
            except Exception as exc:  # the drain read is diagnostic, never the run's verdict
                logger.warning(f"[{worker.key}] drain read failed: {exc!r}")
                continue
            diffs = worker.seeded.expected.diff(actual)
            if diffs:
                logger.warning(
                    f"[{worker.key}] {len(diffs)} residual divergences after drain: "
                    + "; ".join(diff.render() for diff in diffs[:10])
                )
        for client in self._clients:
            await client.close()
        self._clients = []
        logger.info("neighbour load stopped")
        self.raise_if_failed()

    def raise_if_failed(self) -> None:
        if self._failure is not None:
            raise LoadError(f"neighbour load task failed: {self._failure!r}") from self._failure

    async def _all_parked(self) -> tuple[bool, Any]:
        busy = [w.key for w, t in zip(self._workers, self._tasks) if not (w.parked or t.done())]
        return not busy, busy

    # --- the task -----------------------------------------------------------

    async def _run(self, worker: _Worker) -> None:
        rng = random.Random(f"{self._cfg.run_id}/load/{worker.key}")
        interval = 1.0 / self._cfg.neighbour_load_ops_per_second
        try:
            while True:
                worker.parked = True
                await self._running.wait()
                worker.parked = False
                if self._stopping:
                    return
                await self._one_operation(worker, rng)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            # A dead load task must never be tolerated: neighbour integrity would pass vacuously.
            self._failure = exc
            logger.error(f"[{worker.key}] load task failed: {exc!r}")
            self._stopping = True
            self._running.set()
            raise

    def _choose_operation(self, expected: ExpectedSet, rng: random.Random) -> str:
        """Set-size governor: the set oscillates around NEIGHBOUR_SET_TARGET instead of growing."""
        size = len(expected)
        if size == 0:
            return "insert"
        if size < self._cfg.neighbour_set_target:
            return rng.choices(["insert", "update", "delete"], weights=(6, 3, 1))[0]
        return rng.choices(["insert", "update", "delete"], weights=(1, 3, 6))[0]

    async def _one_operation(self, worker: _Worker, rng: random.Random) -> None:
        expected = worker.seeded.expected
        operation = self._choose_operation(expected, rng)
        if operation == "insert":
            uuid = str(uuidlib.UUID(int=rng.getrandbits(128), version=4))
            await self._apply(worker, "insert", uuid, self._object(worker, uuid, rng))
            return
        uuid = rng.choice(expected.uuids())
        if operation == "delete":
            await self._apply(worker, "delete", uuid, None)
            return
        await self._apply(worker, "update", uuid, self._object(worker, uuid, rng))

    def _object(self, worker: _Worker, uuid: str, rng: random.Random) -> ExpectedObject:
        return ExpectedObject(
            uuid=uuid,
            properties=object_properties(worker.seeded.short_name, rng.randrange(10**6)),
            vectors={wvclient.VECTOR_NAME: wvclient.random_vector(rng, self._cfg.vector_dim)},
        )

    async def _apply(
        self, worker: _Worker, operation: str, uuid: str, obj: ExpectedObject | None
    ) -> None:
        data = worker.collection.data
        try:
            if operation == "insert":
                assert obj is not None
                await data.insert(
                    properties=obj.properties,
                    uuid=obj.uuid,
                    vector={wvclient.VECTOR_NAME: obj.vectors[wvclient.VECTOR_NAME]},
                )
            elif operation == "update":
                assert obj is not None
                await data.replace(
                    uuid=obj.uuid,
                    properties=obj.properties,
                    vector={wvclient.VECTOR_NAME: obj.vectors[wvclient.VECTOR_NAME]},
                )
            else:
                await data.delete_by_id(uuid)
        except BaseException as exc:
            await self._handle_failure(worker, operation, uuid, exc)
            return
        # Recorded only after the server acknowledged.
        if operation == "delete":
            worker.seeded.expected.record_delete(uuid)
        elif operation == "insert":
            assert obj is not None
            worker.seeded.expected.record_insert(obj)
        else:
            assert obj is not None
            worker.seeded.expected.record_update(obj)

    async def _handle_failure(
        self, worker: _Worker, operation: str, uuid: str, exc: BaseException
    ) -> None:
        limit = wvclient.usage_limit_rejection(exc)
        if limit is not None:
            # The operator guarantees MAXIMUM_ALLOWED_OBJECTS_COUNT is unset or high enough. It is
            # not readable over REST, so this is the only backstop; swallowing it would throttle the
            # load to zero effective inserts and make neighbour integrity near-vacuous.
            raise LoadError(
                "the rig's MAXIMUM_ALLOWED_OBJECTS_COUNT has been reached, contradicting the "
                f"operator's guarantee ({worker.key}, {operation}): {limit}"
            )
        if isinstance(exc, UnexpectedStatusCodeError) and 400 <= exc.status_code < 500:
            # The server answered before writing anything; the model stays as it is.
            logger.warning(f"[{worker.key}] {operation} {uuid} rejected: {exc!r}")
            return
        if isinstance(exc, (httpx.HTTPError, WeaviateBaseError, OSError, asyncio.TimeoutError)):
            logger.warning(f"[{worker.key}] {operation} {uuid} ambiguous ({exc!r}), reconciling")
            await self._reconcile(worker, uuid)
            return
        raise exc

    async def _reconcile(self, worker: _Worker, uuid: str) -> None:
        """Ask the server what it holds. This test kills no nodes, so a failure here is a defect."""

        async def attempt() -> tuple[bool, Any]:
            try:
                obj = await worker.collection.query.fetch_object_by_id(uuid, include_vector=True)
            except Exception as exc:
                return False, repr(exc)
            if obj is None:
                worker.seeded.expected.record_delete(uuid)
                return True, "absent"
            worker.seeded.expected.record_update(
                ExpectedObject(
                    uuid=str(obj.uuid),
                    properties=dict(obj.properties),
                    vectors={name: list(values) for name, values in (obj.vector or {}).items()},
                )
            )
            return True, "present"

        try:
            await poll(
                attempt,
                deadline_s=self._cfg.raft_visibility_timeout_s,
                interval_s=self._cfg.poll_interval_s,
                describe=f"reconciliation of {worker.key} object {uuid}",
            )
        except Exception as exc:
            raise LoadError(
                f"[{worker.key}] could not reconcile object {uuid}; the model is no longer "
                f"authoritative: {exc!r}"
            ) from exc


class _PausedLoad:
    """Pause on enter, resume on exit — including when the body raises."""

    def __init__(self, load: NeighbourLoad):
        self._load = load

    async def __aenter__(self) -> NeighbourLoad:
        await self._load.pause()
        return self._load

    async def __aexit__(self, *exc: object) -> None:
        await self._load.resume()
