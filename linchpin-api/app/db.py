"""Database connection pool and helpers.

Provides asyncpg pool management, query helpers, and
Postgres LISTEN/NOTIFY utilities for event fanout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any
from weakref import WeakKeyDictionary

import asyncpg

logger = logging.getLogger("linchpin-api.db")

# ---------------------------------------------------------------------------
# Module-level pool reference
# ---------------------------------------------------------------------------

_pool: asyncpg.Pool | None = None


async def create_pool() -> asyncpg.Pool:
    """Create the asyncpg connection pool from DATABASE_URL.

    Stores the pool in module state so it can be retrieved via ``get_pool()``.
    """
    global _pool
    dsn = os.environ.get("DATABASE_URL", "postgresql://linchpin:linchpin@localhost:5432/linchpin")
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=20)
    logger.info("Database connection pool created.")
    return _pool


async def close_pool() -> None:
    """Close the connection pool and clear module state."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed.")


def get_pool() -> asyncpg.Pool:
    """Return the current connection pool.

    Raises ``RuntimeError`` if the pool has not been created yet.
    """
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Call create_pool() first.")
    return _pool


# ---------------------------------------------------------------------------
# Tenant context (RLS) — Atlas M6 / C1 phase-2
# ---------------------------------------------------------------------------
#
# The API layer sets the tenant project on the authenticated request; the
# query helpers below mirror it onto every pooled connection as the
# ``app.current_project`` GUC, which the migration-0030 RLS policies read
# (fail-open: unset GUC = all rows visible, so background jobs and
# pre-auth lookups keep working).

_tenant_project: ContextVar[str | None] = ContextVar(
    "linchpin_tenant_project", default=None
)

# Connection -> GUC value last applied on that connection, so consecutive
# same-tenant requests skip the round-trip entirely. Weak keys: entries
# vanish with the connection when asyncpg discards it (a fresh connection
# really has the GUC unset, which matches the absent entry).
_guc_state: WeakKeyDictionary[asyncpg.Connection, str | None] = WeakKeyDictionary()


def _guc_key(conn: asyncpg.Connection) -> asyncpg.Connection:
    """Return the weak-referenceable object to key *conn* by.

    ``pool.acquire()`` under ``async with`` hands out a PoolConnectionProxy,
    which is ``__slots__``-based and cannot be weak-referenced (weakref on
    it raises TypeError). The real Connection it delegates to is reachable
    via its ``_con`` attribute and *is* weak-referenceable, so cache on
    that instead — same lifetime semantics, since the proxy delegates
    everything (including reset) to it.
    """
    return getattr(conn, "_con", None) or conn


def set_tenant_context(project_id: str | None) -> None:
    """Pin the tenant project for all queries issued from this task.

    Called by the auth dependency on successful authentication; background
    tasks never call it and therefore run fail-open (unset GUC).
    """
    _tenant_project.set(project_id)


async def _apply_tenant_guc(conn: asyncpg.Connection, want: str | None) -> None:
    """Bring *conn*'s ``app.current_project`` GUC in line with *want*."""
    have = _guc_state.get(_guc_key(conn))
    if want == have:
        return
    if want is None:
        # RESET (not set_config('...', '')) — Postgres reads a RESET
        # placeholder GUC as the empty string, which migration 0030's
        # coalesce(..., '') = '' fail-open branch treats as unset.
        await conn.execute("RESET app.current_project")
    else:
        await conn.execute(
            "SELECT set_config('app.current_project', $1, false)", want
        )
    _guc_state[_guc_key(conn)] = want


async def _reset_tenant_guc(conn: asyncpg.Connection) -> None:
    """Clear the GUC before the connection returns to the pool.

    Prevents a residual tenant filter from silently hiding rows from any
    future non-helper user of the connection. Failures are swallowed — a
    broken connection is discarded by the pool anyway.
    """
    try:
        await conn.execute("RESET app.current_project")
        _guc_state[_guc_key(conn)] = None
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def fetch_one(query: str, *args: Any) -> asyncpg.Record | None:
    """Execute *query* and return the first row, or ``None``."""
    pool = get_pool()
    async with pool.acquire() as conn:
        want = _tenant_project.get()
        try:
            await _apply_tenant_guc(conn, want)
            return await conn.fetchrow(query, *args)
        finally:
            if want is not None:
                await _reset_tenant_guc(conn)


async def fetch_all(query: str, *args: Any) -> list[asyncpg.Record]:
    """Execute *query* and return all rows."""
    pool = get_pool()
    async with pool.acquire() as conn:
        want = _tenant_project.get()
        try:
            await _apply_tenant_guc(conn, want)
            return await conn.fetch(query, *args)
        finally:
            if want is not None:
                await _reset_tenant_guc(conn)


async def execute(query: str, *args: Any) -> str:
    """Execute *query* (INSERT / UPDATE / DELETE) and return the status string."""
    pool = get_pool()
    async with pool.acquire() as conn:
        want = _tenant_project.get()
        try:
            await _apply_tenant_guc(conn, want)
            return await conn.execute(query, *args)
        finally:
            if want is not None:
                await _reset_tenant_guc(conn)


# ---------------------------------------------------------------------------
# LISTEN / NOTIFY utilities
# ---------------------------------------------------------------------------
#
# Atlas C2 — per-session channels are converged onto the shared
# ``linchpin_events`` channel (one LISTEN connection per process via the
# app.cluster EventBus) so waiters and SSE streams don't burn one pool
# connection each. Both helpers keep their legacy signatures; callers pass
# per-session channel names like "session_<uuid with '-' replaced by '_'>"
# and the session id is folded into the payload as {"s": ..., "k": ...}.


async def listen(channel: str) -> AsyncIterator[str]:
    """Async generator that yields notification payloads for *channel*.

    Usage::

        async for payload in listen("session_abc"):
            handle(payload)

    With the event bus running (multi-instance mode) this subscribes to an
    in-process queue fed by the single shared-channel LISTEN connection.
    Without the bus it falls back to a dedicated pool connection — listening
    on the shared channel and filtering by session id when *channel* is a
    per-session channel, or on the literal channel name otherwise. The
    connection is released when the generator is closed (e.g. via ``async
    for`` break or ``aclose()``).
    """
    from app import cluster  # deferred — app.cluster imports app.db

    session_id = cluster.parse_session_channel(channel)

    if session_id is not None and cluster.bus_running():
        queue = cluster.subscribe(session_id)
        try:
            while True:
                yield await queue.get()
        finally:
            cluster.unsubscribe(session_id, queue)
        return

    pool = get_pool()
    conn: asyncpg.Connection = await pool.acquire()
    queue: asyncio.Queue[str] = asyncio.Queue()

    def _callback(conn: asyncpg.Connection, pid: int, chan: str, payload: str) -> None:  # noqa: ARG001
        if session_id is not None:
            # Shared channel — only wake this generator for its session.
            # Malformed payloads still wake the consumer, which re-checks
            # the database anyway (a spurious wakeup is never lost data).
            try:
                if json.loads(payload).get("s") != session_id:
                    return
            except Exception:
                pass
        queue.put_nowait(payload)

    target = cluster.EVENT_CHANNEL if session_id is not None else channel
    try:
        await conn.add_listener(target, _callback)
        while True:
            payload = await queue.get()
            yield payload
    finally:
        try:
            await conn.remove_listener(target, _callback)
        except Exception:
            pass  # connection may already be dead
        await pool.release(conn)


async def notify(channel: str, payload: str = "") -> None:
    """Send a notification for *channel* with an optional *payload* kind.

    Per-session channels are delivered as one ``pg_notify`` on the shared
    ``linchpin_events`` channel carrying ``{"s": <session uuid>, "k":
    <payload>}`` (parameterized — no literal escaping needed); same-process
    subscribers additionally receive an immediate local fanout. Non-session
    channels keep the legacy literal NOTIFY.
    """
    from app import cluster  # deferred — app.cluster imports app.db

    session_id = cluster.parse_session_channel(channel)
    if session_id is None:
        pool = get_pool()
        async with pool.acquire() as conn:
            # NOTIFY doesn't support parameterized queries — use literal escaping
            escaped_payload = payload.replace("'", "''")
            await conn.execute(f"NOTIFY {channel}, '{escaped_payload}'")
        return

    body = json.dumps({"s": session_id, "k": payload})
    # Immediate local delivery — same-replica waiters shouldn't depend on
    # the NOTIFY round-trip (the bus delivers its own duplicate later).
    cluster.dispatch_local(session_id, body)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_notify($1, $2)", cluster.EVENT_CHANNEL, body)
