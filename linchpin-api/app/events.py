"""Event store with cursor generation and pagination.

Provides append_event (monotonic seq, opaque cursor, insert + session update)
and get_events (cursor-based pagination with after_cursor, limit, next_cursor).

Validates: Requirements 7.3, 8.1, 8.2, 8.3, 8.4
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone

import asyncpg

from app.db import fetch_all, fetch_one
from app.event_crypto import (
    decrypt_payload,
    encrypt_payload,
    get_session_data_key,
    is_control_event,
)
from app.models import Event, EventResponse, PaginatedEventsResponse

logger = logging.getLogger("linchpin-api.events")


# ---------------------------------------------------------------------------
# Per-session append serialization
# ---------------------------------------------------------------------------
#
# `append_event` computes `next_seq = MAX(seq)+1` and then INSERTs in two
# separate awaits. Pre-PR5 the orchestrator was the sole writer per session,
# so writes were naturally serial within a single asyncio task. PR5 adds a
# second concurrent writer — the deliverables watcher task — for the same
# `session_id`. The two tasks can interleave between the SELECT and the
# INSERT, both compute the same next_seq, and the loser raises
# `UniqueViolationError` against the events `PRIMARY KEY (session_id, seq)`.
#
# A per-session `asyncio.Lock` serializes the SELECT+INSERT pair so writers
# in this process see a monotonic seq. Atlas C2 — cross-replica writers can
# still race on MAX(seq)+1; `append_event` catches the resulting
# UniqueViolationError and retries with a fresh MAX(seq) read (bounded).

_session_event_locks: dict[uuid.UUID, asyncio.Lock] = {}


def _lock_for(session_id: uuid.UUID) -> asyncio.Lock:
    lock = _session_event_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_event_locks[session_id] = lock
    return lock


def release_session_event_lock(session_id: str) -> None:
    """Drop the per-session lock once the session is terminated.

    Called from `terminate_session` so the dict doesn't grow unboundedly
    across the api process's lifetime.
    """
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return
    _session_event_locks.pop(sid, None)


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------


def encode_cursor(seq: int) -> str:
    """Encode a sequence number into an opaque base64 cursor string."""
    return base64.urlsafe_b64encode(str(seq).encode()).decode()


def decode_cursor(cursor: str) -> int:
    """Decode an opaque base64 cursor string back to a sequence number."""
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, Exception) as exc:
        raise ValueError(f"Invalid cursor: {cursor}") from exc


# ---------------------------------------------------------------------------
# append_event
# ---------------------------------------------------------------------------


async def append_event(
    session_id: str,
    event_type: str,
    payload: dict,
) -> Event:
    """Append an event to a session's event log.

    - Assigns the next monotonic seq for this session.
    - Generates an opaque cursor (base64-encoded seq).
    - Inserts into the events table.
    - Updates the session's last_event_cursor and stats.
    - Returns the Event model.
    """
    sid = uuid.UUID(session_id)

    # Atlas B8 — business-content payloads of encrypted sessions are sealed
    # into the {"enc": "v1", "ct": ...} envelope at rest. Control events
    # (session.* / span.* / thread.*) and plaintext sessions pass through
    # unchanged. The returned Event model keeps the plaintext payload, so
    # callers (webhook fan-out included) see the original shape.
    stored_payload: dict = payload
    if not is_control_event(event_type):
        data_key = await get_session_data_key(sid)
        if data_key is not None:
            stored_payload = encrypt_payload(payload, data_key)

    async with _lock_for(sid):
        # Get next seq number and insert. Atlas C2 — cross-replica writers
        # race on MAX(seq)+1 (the in-process lock above only serializes
        # this process's tasks); the loser hits the events-table
        # PRIMARY KEY (session_id, seq) and retries with a fresh read.
        event_row = None
        cursor = ""
        for attempt in range(3):
            row = await fetch_one(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM events WHERE session_id = $1",
                sid,
            )
            next_seq: int = row["next_seq"]
            cursor = encode_cursor(next_seq)

            # Insert event
            payload_json = json.dumps(stored_payload)
            try:
                event_row = await fetch_one(
                    """
                    INSERT INTO events (session_id, cursor, seq, type, payload)
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    RETURNING *
                    """,
                    sid,
                    cursor,
                    next_seq,
                    event_type,
                    payload_json,
                )
                break
            except asyncpg.UniqueViolationError:
                if attempt >= 2:
                    raise
                logger.warning(
                    "Event seq collision for session %s (attempt %d); retrying",
                    session_id, attempt + 1,
                )

        # Update session stats and last_event_cursor. Kept inside the lock so
        # `last_event_cursor` advances monotonically with the seq we just
        # inserted — otherwise a faster concurrent writer could overwrite it
        # with an earlier cursor.
        if event_type == "agent.tool_use":
            stats_expr = (
                "jsonb_set("
                "jsonb_set(stats, '{total_events}', to_jsonb((stats->>'total_events')::int + 1)), "
                "'{tool_calls}', to_jsonb((stats->>'tool_calls')::int + 1))"
            )
        elif event_type == "agent.message":
            stats_expr = (
                "jsonb_set("
                "jsonb_set(stats, '{total_events}', to_jsonb((stats->>'total_events')::int + 1)), "
                "'{model_turns}', to_jsonb((stats->>'model_turns')::int + 1))"
            )
        else:
            stats_expr = "jsonb_set(stats, '{total_events}', to_jsonb((stats->>'total_events')::int + 1))"

        await fetch_one(
            f"""
            UPDATE sessions
            SET last_event_cursor = $1,
                stats = {stats_expr},
                updated_at = now()
            WHERE id = $2
            RETURNING id
            """,
            cursor,
            sid,
        )

    event = Event(
        session_id=str(event_row["session_id"]),
        cursor=event_row["cursor"],
        seq=event_row["seq"],
        type=event_row["type"],
        # Atlas B8 — echo the plaintext payload the caller supplied; the
        # stored row may carry the encrypted envelope. Webhook fan-out below
        # therefore delivers the original shape to subscribers.
        payload=payload,
        processed_at=event_row["processed_at"],
        # Atlas A3 (0017) — DB-default identity; ``get`` tolerates
        # pre-0017 rows/snapshots lacking the column.
        event_id=event_row.get("event_id"),
    )

    # v0.2.0 item #14 — fan webhook-relevant events out to subscribers.
    # Imported lazily so a fresh DB without the webhooks table (legacy
    # snapshots, smoke-test fixtures) doesn't crash event emission.
    try:
        from app.webhooks import enqueue_event

        await enqueue_event(event_type, {
            "session_id": event.session_id,
            "cursor": event.cursor,
            "seq": event.seq,
            "type": event.type,
            "payload": event.payload,
        })
    except Exception:
        # Webhook fan-out must never break the agent loop. Logged in webhooks.
        pass

    return event


# ---------------------------------------------------------------------------
# get_events
# ---------------------------------------------------------------------------


async def get_events(
    session_id: str,
    after_cursor: str | None = None,
    limit: int = 50,
    types: list[str] | None = None,
) -> PaginatedEventsResponse:
    """Retrieve events for a session with cursor-based pagination.

    - If after_cursor is provided, returns events with seq > decoded cursor seq.
    - If types is provided (v0.2.0 item #12), returns only events whose ``type``
      is in the list. Validation of type strings against ``EVENT_TYPES`` is the
      route handler's job — this helper passes the list through to the SQL
      ``WHERE type = ANY($N)`` clause as-is.
    - Results ordered by seq ASC.
    - Fetches limit + 1 rows to detect has_more / next_cursor.
    """
    sid = uuid.UUID(session_id)

    # Build the query incrementally based on which filters are active. Using
    # $N positional args keeps asyncpg happy; the alternative (string-interp
    # of the WHERE) would re-introduce SQL injection risk.
    where_clauses = ["session_id = $1"]
    params: list = [sid]
    if after_cursor is not None:
        params.append(decode_cursor(after_cursor))
        where_clauses.append(f"seq > ${len(params)}")
    if types:
        params.append(types)
        where_clauses.append(f"type = ANY(${len(params)})")
    params.append(limit + 1)
    limit_ph = f"${len(params)}"

    sql = (
        "SELECT * FROM events WHERE "
        + " AND ".join(where_clauses)
        + f" ORDER BY seq ASC LIMIT {limit_ph}"
    )
    rows = await fetch_all(sql, *params)

    has_more = len(rows) > limit
    result_rows = rows[:limit]

    # Atlas B8 — decrypt sealed payloads for API/SSE consumers. One key
    # unwrap per page; plaintext control events pass through untouched.
    data_key = await get_session_data_key(sid)

    events = [
        EventResponse(
            session_id=str(r["session_id"]),
            cursor=r["cursor"],
            seq=r["seq"],
            type=r["type"],
            payload=decrypt_payload(
                json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
                data_key,
            ),
            processed_at=r["processed_at"],
            # Atlas A3 (0017) — DB-default identity.
            event_id=r.get("event_id"),
        )
        for r in result_rows
    ]

    next_cursor = events[-1].cursor if has_more and events else None

    return PaginatedEventsResponse(events=events, next_cursor=next_cursor)
