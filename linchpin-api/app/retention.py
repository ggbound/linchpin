"""Atlas B12 — events retention (PRD §7).

Hourly cleanup loop deletes event rows older than
``LINCHPIN_EVENTS_RETENTION_DAYS`` (default 90). Set the variable to ``0``
or a negative number to disable retention entirely. Per-session ``seq``
monotonicity is untouched — deletion is by age, and cursors of surviving
rows never move, so client cursors never regress.
"""

from __future__ import annotations

import asyncio
import logging
import os

from app.db import execute

logger = logging.getLogger("linchpin-api.retention")

_DEFAULT_RETENTION_DAYS = 90


def get_retention_days() -> int:
    """Parse ``LINCHPIN_EVENTS_RETENTION_DAYS``; <=0 disables retention."""
    raw = os.environ.get("LINCHPIN_EVENTS_RETENTION_DAYS", "")
    try:
        return int(raw) if raw.strip() else _DEFAULT_RETENTION_DAYS
    except ValueError:
        logger.warning(
            "invalid LINCHPIN_EVENTS_RETENTION_DAYS=%r — falling back to %d",
            raw, _DEFAULT_RETENTION_DAYS,
        )
        return _DEFAULT_RETENTION_DAYS


async def cleanup_expired_events() -> None:
    """Delete event rows older than the retention window."""
    days = get_retention_days()
    if days <= 0:
        return  # retention disabled
    status = await execute(
        "DELETE FROM events WHERE created_at < now() - ($1 || ' days')::interval",
        str(days),
    )
    try:
        count = int(str(status).split()[-1])
    except (ValueError, IndexError):
        count = -1
    logger.info("events retention: deleted %d event row(s) older than %dd", count, days)


async def cleanup_loop(interval_sec: float = 3600.0) -> None:
    """Long-running task: hourly retention pass. Spawned from ``main.lifespan``."""
    days = get_retention_days()
    if days <= 0:
        logger.info("events retention: disabled (LINCHPIN_EVENTS_RETENTION_DAYS<=0)")
        return
    logger.info(
        "events retention: cleanup loop started (interval=%.0fs, days=%d)",
        interval_sec, days,
    )
    while True:
        try:
            await cleanup_expired_events()
        except asyncio.CancelledError:
            logger.info("events retention: cleanup loop cancelled, exiting")
            raise
        except Exception:
            logger.exception("events retention: cleanup pass failed")
        await asyncio.sleep(interval_sec)
