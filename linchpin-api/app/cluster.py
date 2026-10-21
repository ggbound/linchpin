"""Atlas C2 — cluster primitives for multi-replica deployments.

Three pieces that turn the single-process runtime into a stateless API tier
that can run as several replicas against one Postgres, without a separate
coordinator service:

- ``instance_id`` — stable identity of this replica, stamped on every
  session it orchestrates (``sessions.orchestrator_instance``).
- ``SessionLease`` — exclusive per-session orchestrator ownership backed by
  a Postgres advisory lock. The lock lives on a *dedicated* connection, so
  Postgres releases it the moment the owning process dies or the connection
  drops — exactly the crash-safety property a lease needs. Holding the
  lease also heartbeats ``sessions.orchestrator_heartbeat``; a replica that
  stops renewing loses the session to the supervisor (FR-2.3 "worker crash
  → automatic migration").
- ``EventBus`` — one LISTEN connection per process on the shared
  ``linchpin_events`` channel, demultiplexed in-process by session id.

Single-process / test runs without a DB pool degrade to the pre-C2
semantics: lease acquisition is a no-op (``app.orchestrator`` treats a
missing pool as "run unleased") and the per-session asyncio locks plus the
events-table ``PRIMARY KEY (session_id, seq)`` remain the correctness
story.

Residual risks (accepted, bounded):

- Between a lease going stale and the supervisor's next pass, the old owner
  may still be finishing an in-flight turn. Both writers append through
  ``append_event``; the events-table PK keeps the log consistent.
- A blocked ``wait_for_confirmation`` on the old owner only wakes on the
  next notification; until then the taken-over session's new orchestrator
  waits for input, so at worst a turn is duplicated, never corrupted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

import asyncpg

from app.db import get_pool

logger = logging.getLogger("linchpin-api.cluster")

# The one channel everything NOTIFYs on. Postgres LISTEN has no wildcards,
# so per-session channels would burn one pool connection per waiting
# consumer (orchestrator waiters, SSE streams) and exhaust max_connections.
EVENT_CHANNEL = "linchpin_events"

# Signed 64-bit range for advisory lock keys.
_ADVISORY_MASK = 0x7FFFFFFFFFFFFFFF

_instance_id: str | None = None
_bus: "EventBus | None" = None


# ---------------------------------------------------------------------------
# Instance identity
# ---------------------------------------------------------------------------


def get_instance_id() -> str:
    """Return this replica's id, materializing it on first use.

    ``LINCHPIN_INSTANCE_ID`` pins it (useful for deployments that want
    stable identity across restarts, e.g. container-name derived); a random
    suffix otherwise guarantees two replicas never share an id.
    """
    global _instance_id
    if _instance_id is None:
        _instance_id = (
            os.environ.get("LINCHPIN_INSTANCE_ID")
            or f"api-{uuid.uuid4().hex[:12]}"
        )
    return _instance_id


def set_instance_id(instance_id: str) -> None:
    """Override the instance id (tests, deployments with explicit wiring)."""
    global _instance_id
    _instance_id = instance_id


def advisory_key(session_id: str | uuid.UUID) -> int:
    """Map a session UUID onto a signed 64-bit advisory-lock key.

    Two distinct UUIDs may collide on a key (birthday bound ~2^63); a
    collision only means two unrelated sessions briefly serialize their
    lock attempts — harmless.
    """
    if isinstance(session_id, str):
        session_id = uuid.UUID(session_id)
    return session_id.int & _ADVISORY_MASK


# ---------------------------------------------------------------------------
# Channel helpers
# ---------------------------------------------------------------------------


def session_channel(session_id: str) -> str:
    """Build the per-session channel name (Postgres ids may not contain '-')."""
    return f"session_{session_id.replace('-', '_')}"


def parse_session_channel(channel: str) -> str | None:
    """Recover the canonical session UUID from a per-session channel name.

    Hex digits never contain ``_``, so replacing ``_`` back to ``-`` is
    reversible. Returns ``None`` for non-session channels.
    """
    if not channel.startswith("session_"):
        return None
    candidate = channel[len("session_"):].replace("_", "-")
    try:
        return str(uuid.UUID(candidate))
    except ValueError:
        return None


class LeaseHeldElsewhere(Exception):
    """Raised when another replica currently owns the session lease."""


# ---------------------------------------------------------------------------
# SessionLease — advisory-lock backed orchestrator ownership
# ---------------------------------------------------------------------------


class SessionLease:
    """Exclusive per-session orchestrator lease.

    The advisory lock is bound to a dedicated pool connection: it cannot be
    passed through the fetch_one/execute helpers (which acquire and release
    per query — that would release the lock after every statement). When the
    connection dies, Postgres releases the lock automatically, so a crashed
    replica never holds a lease. While held, the lease heartbeats
    ``orchestrator_heartbeat`` so a supervisor can distinguish "owned by a
    live replica" from "abandoned by a dead one".
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = str(session_id)
        self._conn: asyncpg.Connection | None = None
        self.broken = False

    @property
    def held(self) -> bool:
        return self._conn is not None and not self.broken

    async def acquire(self) -> bool:
        """Try to take the lock. Returns ``False`` when held elsewhere.

        Raises when the pool/database is unavailable (callers decide whether
        that means skip or degrade to unleased single-process mode).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            acquired = await conn.fetchval(
                "SELECT pg_try_advisory_lock($1::bigint)",
                advisory_key(self.session_id),
            )
        except Exception:
            await pool.release(conn)
            raise
        if not acquired:
            await pool.release(conn)
            return False
        self._conn = conn
        try:
            await self._stamp()
        except Exception:
            # Connection unusable right after locking — the lock will die
            # with it. Surface the error; we never claimed ownership.
            self.broken = True
            await self.release()
            raise
        return True

    async def _stamp(self) -> None:
        if self._conn is None:
            return
        await self._conn.execute(
            "UPDATE sessions SET orchestrator_instance = $1, "
            "orchestrator_heartbeat = now() WHERE id = $2",
            get_instance_id(),
            uuid.UUID(self.session_id),
        )

    async def renew(self) -> bool:
        """Heartbeat: re-stamp ownership while holding the lock.

        Returns ``False`` when the lease is broken (connection died) — the
        caller must stop orchestrating and release. Postgres has already
        released the lock in that case, so another replica can take over.
        """
        if self._conn is None or self.broken:
            return False
        try:
            await self._stamp()
            return True
        except Exception:
            self.broken = True
            return False

    async def release(self) -> None:
        """Release the lock and return the connection to the pool."""
        conn, self._conn = self._conn, None
        if conn is None:
            return
        # Clear the bookkeeping only if we still own it (a takeover may
        # already have re-stamped the row to another instance).
        try:
            await conn.execute(
                "UPDATE sessions SET orchestrator_instance = NULL, "
                "orchestrator_heartbeat = NULL "
                "WHERE id = $1 AND orchestrator_instance = $2",
                uuid.UUID(self.session_id),
                get_instance_id(),
            )
        except Exception:
            pass  # connection may already be dead; the lock died with it
        try:
            await conn.execute(
                "SELECT pg_advisory_unlock($1::bigint)",
                advisory_key(self.session_id),
            )
        except Exception:
            pass
        try:
            await get_pool().release(conn)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# EventBus — one LISTEN connection per process
# ---------------------------------------------------------------------------


class EventBus:
    """Single-connection LISTEN on ``linchpin_events``, fanned out in-process.

    Every notification carries ``{"s": <session uuid>, "k": <kind>}``;
    subscribers are per-session ``asyncio.Queue``s handed out by
    :func:`subscribe` (consumed by ``app.db.listen``). Malformed payloads
    are dropped — consumers re-check the database after every wakeup, so a
    lost wakeup degrades to latency, never to lost data.
    """

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[str]]] = {}
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # -- subscription management (event-loop only; no lock needed) --------

    def subscribe(self, session_id: str) -> "asyncio.Queue[str]":
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._subs.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: "asyncio.Queue[str]") -> None:
        queues = self._subs.get(session_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                self._subs.pop(session_id, None)

    def dispatch(self, session_id: str, payload: str) -> None:
        for queue in self._subs.get(session_id, ()):
            queue.put_nowait(payload)

    # -- lifecycle ---------------------------------------------------------

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="linchpin-event-bus")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Event bus task ended with error", exc_info=True)
        self._task = None

    # -- connection loop ---------------------------------------------------

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopping.is_set():
            conn: asyncpg.Connection | None = None
            callback = None
            try:
                pool = get_pool()
                conn = await pool.acquire()
                queue: asyncio.Queue[str] = asyncio.Queue()

                def callback(_conn: Any, _pid: int, _chan: str, payload: str) -> None:  # noqa: ARG001
                    queue.put_nowait(payload)

                await conn.add_listener(EVENT_CHANNEL, callback)
                logger.info(
                    "Event bus listening (channel=%s, instance=%s)",
                    EVENT_CHANNEL, get_instance_id(),
                )
                backoff = 1.0
                while not self._stopping.is_set():
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue  # re-check the stop flag periodically
                    self._demultiplex(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Event bus connection lost; reconnecting in %.0fs", backoff,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                if conn is not None:
                    try:
                        if callback is not None:
                            await conn.remove_listener(EVENT_CHANNEL, callback)
                    except Exception:
                        pass
                    try:
                        await get_pool().release(conn)
                    except Exception:
                        pass

    def _demultiplex(self, payload: str) -> None:
        try:
            session_id = json.loads(payload).get("s")
        except Exception:
            return
        if not isinstance(session_id, str):
            return
        self.dispatch(session_id, payload)


# ---------------------------------------------------------------------------
# Module-level bus handle
# ---------------------------------------------------------------------------


def bus_running() -> bool:
    return _bus is not None and _bus.running()


def subscribe(session_id: str) -> "asyncio.Queue[str]":
    assert _bus is not None
    return _bus.subscribe(session_id)


def unsubscribe(session_id: str, queue: "asyncio.Queue[str]") -> None:
    if _bus is not None:
        _bus.unsubscribe(session_id, queue)


def dispatch_local(session_id: str, payload: str) -> None:
    """Fan a payload out to this process's subscribers immediately.

    Called by ``app.db.notify`` so same-replica wakeups don't wait for the
    Postgres round-trip (remote replicas get it via NOTIFY). The bus will
    also deliver its own copy — duplicate wakeups are harmless; consumers
    re-query the database.
    """
    if _bus is not None:
        _bus.dispatch(session_id, payload)


async def start_event_bus() -> None:
    """Start the per-process event bus. No-op without a DB pool (tests)."""
    global _bus
    try:
        get_pool()
    except RuntimeError:
        logger.info("No DB pool — event bus disabled (single-process mode).")
        return
    if _bus is None:
        _bus = EventBus()
    await _bus.start()


async def stop_event_bus() -> None:
    global _bus
    if _bus is not None:
        await _bus.stop()
        _bus = None
