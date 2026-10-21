"""Atlas B7 — deployment scheduler (FR-8.1).

A deployment is a cron-triggered run configuration: agent + environment
+ a cron expression in a named IANA timezone + the initial event seed
each scheduled session starts with. The lifecycle:

1. CRUD via ``/v1/deployments`` (routes/deployments.py).
2. A long-running scheduler loop (spawned from ``main.lifespan``) polls
   ``status='active' AND archived_at IS NULL AND next_run_at <= NOW()``,
   claiming due rows with ``SELECT ... FOR UPDATE SKIP LOCKED`` so
   multiple API processes don't double-trigger.
3. Each claimed deployment creates a real session through the same
   ``create_session`` machinery the HTTP API uses (validation, container
   provisioning, orchestrator task), stamps it with ``deployment_id``,
   seeds ``initial_events``, and recomputes ``next_run_at`` from the
   *scheduled* time — cron semantics: the next fire time follows the
   plan, not the actual run, so a delayed trigger doesn't drift the
   schedule.

M3 scope: single-process asyncio loop. M4 migrates the schedule source
of truth to Temporal schedules (C2/C3); the table shape is chosen so
that migration is additive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from croniter import croniter
from fastapi import Response
from starlette.requests import Request as StarletteRequest
from zoneinfo import ZoneInfo

from app.auth import AuthContext
from app.db import execute, fetch_all, fetch_one
from app.models import CreateSessionRequest

logger = logging.getLogger("linchpin-api.deployments")

# How far a claimed row's next_run_at is pushed out while it's being
# worked on — same contention trick as webhook_deliveries.claim_pending_batch.
_CLAIM_PARK_INTERVAL = "1 hour"

# Event types a deployment may seed into its scheduled sessions. Only
# user-side inputs — an attacker (or typo) must not be able to forge
# agent.* / session.* events via initial_events.
_ALLOWED_INITIAL_EVENT_TYPES = ("user.message", "user.steer")


def validate_timezone(tz: str) -> None:
    """Raise ``ValueError`` when ``tz`` is not a valid IANA zone name."""
    try:
        ZoneInfo(tz)
    except Exception as exc:  # ZoneInfoNotFoundError + bad keys
        raise ValueError(f"invalid timezone: {tz!r}") from exc


def validate_cron(cron: str) -> None:
    """Raise ``ValueError`` when ``cron`` isn't a parseable 5-field cron."""
    try:
        croniter(cron)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"invalid cron expression: {cron!r}") from exc


def compute_next_run(
    cron: str,
    tz: str,
    *,
    after: datetime | None = None,
) -> datetime:
    """Next fire time for ``cron`` in ``tz``, strictly after ``after``.

    ``after`` defaults to now. Croniter is timezone-aware: the base
    datetime is converted into the deployment's zone before evaluation
    so wall-clock expressions (``0 9 * * 1-5``) follow local 9am, and
    the returned aware datetime is normalized to UTC for storage.
    """
    validate_cron(cron)
    validate_timezone(tz)
    zone = ZoneInfo(tz)
    base = (after or datetime.now(timezone.utc)).astimezone(zone)
    nxt = croniter(cron, base).get_next(datetime)
    return nxt.astimezone(timezone.utc)


def validate_initial_events(events: list[dict[str, Any]]) -> None:
    """Raise ``ValueError`` when an initial_events entry is malformed.

    Each entry must be ``{"type": ..., "payload": {...}}`` with a
    user-side event type (see _ALLOWED_INITIAL_EVENT_TYPES).
    """
    for idx, item in enumerate(events):
        if not isinstance(item, dict):
            raise ValueError(f"initial_events[{idx}] must be an object")
        etype = item.get("type")
        if not isinstance(etype, str) or etype not in _ALLOWED_INITIAL_EVENT_TYPES:
            raise ValueError(
                f"initial_events[{idx}].type must be one of "
                f"{', '.join(_ALLOWED_INITIAL_EVENT_TYPES)}"
            )
        if not isinstance(item.get("payload", {}), dict):
            raise ValueError(f"initial_events[{idx}].payload must be an object")


async def claim_due_deployments(limit: int = 10) -> list[dict]:
    """Atomically claim due deployments (FOR UPDATE SKIP LOCKED).

    While claimed, ``next_run_at`` is parked 1h out of contention — the
    trigger path recomputes the real next fire time before releasing.
    Returns each row's *scheduled* due time (``due_at``) so the
    recompute anchors to the plan rather than the actual run.
    """
    rows = await fetch_all(
        f"""
        WITH claimed AS (
          SELECT id, next_run_at AS due_at
          FROM deployments
          WHERE status = 'active'
            AND archived_at IS NULL
            AND next_run_at IS NOT NULL
            AND next_run_at <= NOW()
          ORDER BY next_run_at
          LIMIT $1
          FOR UPDATE SKIP LOCKED
        )
        UPDATE deployments d
        SET next_run_at = NOW() + INTERVAL '{_CLAIM_PARK_INTERVAL}'
        FROM claimed
        WHERE d.id = claimed.id
        RETURNING d.id, d.project_id, d.agent_id, d.environment_id,
                  d.cron, d.timezone, d.initial_events, claimed.due_at
        """,
        limit,
    )
    return list(rows)


async def trigger_deployment(dep: dict, app: Any) -> str:
    """Create the scheduled session for one claimed deployment.

    Reuses the HTTP ``create_session`` machinery (the spawn_thread
    precedent): a system AuthContext is derived from the deployment's
    project, and a synthetic Request carries ``app.state`` (sandbox,
    orchestrator_tasks, …) into the route function. On success the
    session is stamped ``deployment_id``, seeded with ``initial_events``
    and the deployment's ``next_run_at`` is recomputed from ``due_at``.
    """
    dep_id = str(dep["id"])

    # The agent must still exist (archived agents refuse sessions via the
    # normal validation path). Read its project for the system AuthContext.
    agent_row = await fetch_one(
        """
        SELECT a.project_id, p.org_id
        FROM agents a JOIN projects p ON p.id = a.project_id
        WHERE a.id = $1
        """,
        dep["agent_id"],
    )
    if agent_row is None:
        logger.warning(
            "deployments: %s due but agent %s vanished; rescheduling only",
            dep_id, dep["agent_id"],
        )
        await _recompute_next_run(dep)
        return "agent_missing"

    ctx = AuthContext(
        project_id=str(agent_row["project_id"]),
        org_id=str(agent_row["org_id"]),
        api_key_id=None,
        scopes=("*",),
    )

    # Synthetic request: create_session only reads request.app.state.*
    # (sandbox, task dicts) and request.state.api_version (defaults to V1).
    request = StarletteRequest({
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "app": app,
        "state": {},
    })

    from app.routes.sessions import create_session

    body = CreateSessionRequest(
        agent_id=str(dep["agent_id"]),
        environment_id=str(dep["environment_id"]),
        metadata={"deployment_id": dep_id},
    )
    try:
        # Explicit idempotency_key/events_encrypted/kek_credential_id: an
        # internal direct call does not go through FastAPI DI, so the
        # Header() FieldInfo defaults would leak in as truthy values
        # (spawn_thread precedent).
        session = await create_session(
            body, request, Response(),
            idempotency_key=None, events_encrypted=False,
            kek_credential_id=None, ctx=ctx,
        )  # type: ignore[arg-type]
    except Exception as exc:
        logger.error(
            "deployments: %s trigger failed: %s; rescheduling only",
            dep_id, exc,
        )
        await _recompute_next_run(dep)
        return "error"

    session_id = session.id
    try:
        await execute(
            "UPDATE sessions SET deployment_id = $1 WHERE id = $2",
            dep["id"],
            uuid.UUID(session_id),
        )
    except Exception:
        logger.exception(
            "deployments: failed to stamp deployment_id on session %s", session_id,
        )

    # Seed the scheduled session with its initial events (best-effort —
    # the session exists either way, mirroring spawn_thread's input_message).
    for item in _parse_initial_events(dep["initial_events"]):
        try:
            from app.orchestrator import append_event
            await append_event(session_id, item["type"], item["payload"])
        except Exception:
            logger.exception(
                "deployments: failed to seed %s into session %s",
                item["type"], session_id,
            )

    await _recompute_next_run(dep)
    logger.info(
        "deployments: %s triggered session %s", dep_id, session_id,
    )
    return "triggered"


def _parse_initial_events(raw: Any) -> list[dict[str, Any]]:
    """asyncpg hands JSONB back as a list already; be lenient about str."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [item for item in (raw or []) if isinstance(item, dict)]


async def _recompute_next_run(dep: dict) -> None:
    """Recompute next_run_at from the scheduled due time (cron semantics)."""
    try:
        nxt = compute_next_run(dep["cron"], dep["timezone"], after=dep["due_at"])
    except ValueError:
        # A cron expression that parses at create time but yields no next
        # match (or a zone that vanished) — park it so it stops firing.
        logger.error(
            "deployments: %s has unparsable cron/timezone; parking", dep["id"],
        )
        await execute(
            "UPDATE deployments SET next_run_at = NULL WHERE id = $1",
            dep["id"],
        )
        return
    try:
        await execute(
            "UPDATE deployments SET next_run_at = $1 WHERE id = $2",
            nxt,
            dep["id"],
        )
    except Exception:
        logger.exception(
            "deployments: failed to persist next_run_at for %s", dep["id"],
        )


async def deployments_scheduler_loop(
    app: Any,
    *,
    poll_interval: float = 5.0,
) -> None:
    """Long-running task: trigger due deployments forever.

    Spawned from ``main.lifespan`` behind ``scheduler_enabled()``. The
    default poll interval is 5s (minute-granularity cron doesn't need
    to-the-second latency); each tick processes up to 10 due rows.
    """
    logger.info(
        "deployments: scheduler started (poll=%.1fs)", poll_interval,
    )
    while True:
        try:
            due = await claim_due_deployments()
            for dep in due:
                try:
                    result = await trigger_deployment(dep, app)
                    logger.debug(
                        "deployments: %s → %s", dep["id"], result,
                    )
                except Exception:
                    logger.exception(
                        "deployments: trigger crashed for %s", dep["id"],
                    )
        except asyncio.CancelledError:
            logger.info("deployments: scheduler cancelled, exiting")
            raise
        except Exception:
            logger.exception("deployments: scheduler loop tick failed")
        await asyncio.sleep(poll_interval)


def scheduler_enabled() -> bool:
    """Whether the deployment scheduler should run in this process.

    Controlled by ``LINCHPIN_DEPLOYMENTS_SCHEDULER``; default ``true``.
    Tests use ``false`` to keep the background loop out of the way.
    """
    return os.environ.get(
        "LINCHPIN_DEPLOYMENTS_SCHEDULER", "true"
    ).lower() != "false"
