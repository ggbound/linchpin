"""Atlas A8 — idempotency keys for POST /v1/sessions and
POST /v1/sessions/{id}/events (PRD FR-1.3).

A client sending an ``Idempotency-Key`` header gets exactly-once
semantics for the successful case: the first completed response is
stored and any retry with the same key + same request body replays it
instead of re-executing. Reusing a key with a *different* body is a
409 (matches Stripe's ``idempotency_error`` semantics).

Only 2xx responses are recorded — 4xx outcomes are deterministic from
the session's current state, so a retry re-derives them safely.

Rows expire after ``TTL_HOURS`` (24); ``cleanup_loop`` (spawned from
``main.lifespan``) deletes stale rows hourly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from app.db import execute, fetch_one

logger = logging.getLogger("linchpin-api.idempotency")

TTL_HOURS = 24


class IdempotencyHashMismatch(Exception):
    """The key was previously used with a different request body."""


def compute_request_hash(method: str, path: str, body: Any) -> str:
    """Stable hash of method + path + canonicalized JSON body."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    material = f"{method} {path} {canonical}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def replay(
    key: str | None, endpoint: str, request_hash: str
) -> tuple[int, dict | list] | None:
    """Return ``(status, body)`` of the stored response for a completed
    request, or ``None`` when the key is unknown/incomplete.

    Raises ``IdempotencyHashMismatch`` when the key exists but was used
    with a different request.
    """
    if not key:
        return None
    row = await fetch_one(
        "SELECT request_hash, response_status, response_body "
        "FROM idempotency_keys WHERE key = $1 AND endpoint = $2",
        key,
        endpoint,
    )
    if row is None:
        return None
    if row["request_hash"] != request_hash:
        raise IdempotencyHashMismatch(key)
    status = row["response_status"]
    body = row["response_body"]
    if status is None or body is None:
        return None  # no completed response recorded yet — treat as miss
    if isinstance(body, str):
        body = json.loads(body)
    return int(status), body


async def record(
    key: str | None,
    endpoint: str,
    request_hash: str,
    status: int,
    body: Any,
) -> None:
    """Persist a completed response for *key*. Failures are logged and
    swallowed — idempotency storage must never break the main path."""
    if not key:
        return
    try:
        await execute(
            """
            INSERT INTO idempotency_keys
                (key, endpoint, request_hash, response_status, response_body)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (key) DO NOTHING
            """,
            key,
            endpoint,
            request_hash,
            status,
            json.dumps(body, default=str),
        )
    except Exception:
        logger.exception("idempotency: failed to record key %s", key)


async def cleanup_expired_keys() -> None:
    """Delete idempotency records older than the TTL."""
    await execute(
        f"DELETE FROM idempotency_keys "
        f"WHERE created_at < now() - interval '{TTL_HOURS} hours'"
    )


async def cleanup_loop(interval_sec: float = 3600.0) -> None:
    """Long-running task: hourly TTL pass. Spawned from ``main.lifespan``."""
    logger.info("idempotency: cleanup loop started (interval=%.0fs)", interval_sec)
    while True:
        try:
            await cleanup_expired_keys()
        except asyncio.CancelledError:
            logger.info("idempotency: cleanup loop cancelled, exiting")
            raise
        except Exception:
            logger.exception("idempotency: cleanup pass failed")
        await asyncio.sleep(interval_sec)
