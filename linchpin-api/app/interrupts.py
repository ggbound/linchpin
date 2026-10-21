"""Atlas B2 — in-process per-session interrupt flags.

The DB-based ``check_for_interrupt`` (last event is ``user.interrupt``) is
too heavy to run per streaming chunk and per tool dispatch. Instead,
``post_events`` sets a lightweight in-process flag when it persists a
``user.interrupt``, and the orchestrator's hot paths (streaming loop, tool
dispatch race) poll the flag. Consumers clear the flag when they act on it
so a stale set can never leak into the next turn.

Single-process runtime: the API and the orchestrator share one event loop,
so a plain dict of ``asyncio.Event`` objects is sufficient. No cross-process
delivery is needed — ``post_events`` already NOTIFYs the session channel,
and the orchestrator's LISTEN wakeup re-checks the DB as a fallback.
"""

from __future__ import annotations

import asyncio

# session_id (str) -> interrupt event for that session
_flags: dict[str, asyncio.Event] = {}


def request_interrupt(session_id: str) -> None:
    """Mark *session_id* as interrupted (get-or-create the event, then set)."""
    ev = _flags.get(session_id)
    if ev is None:
        ev = asyncio.Event()
        _flags[session_id] = ev
    ev.set()


def is_interrupted(session_id: str) -> bool:
    """Return True when an interrupt is pending for *session_id*."""
    ev = _flags.get(session_id)
    return ev is not None and ev.is_set()


def event(session_id: str) -> asyncio.Event:
    """Return the interrupt event for *session_id*, creating it if needed.

    Used by the tool-dispatch race (``await event.wait()``) so the waiter
    exists before the interrupt arrives.
    """
    ev = _flags.get(session_id)
    if ev is None:
        ev = asyncio.Event()
        _flags[session_id] = ev
    return ev


def clear_interrupt(session_id: str) -> None:
    """Drop the flag for *session_id* (called when the interrupt is consumed)."""
    _flags.pop(session_id, None)
