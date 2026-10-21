"""Atlas B10 — Prometheus metrics.

Two layers:

1. HTTP metrics via ``prometheus-fastapi-instrumentator`` (request
   count/duration histogram) exposed on ``/metrics`` — mounted on the
   root app (not ``/v1``) so scrapers don't need a bearer token.
2. Four custom gauges required by the upgrade plan:
   - ``linchpin_active_sessions``          — DB: sessions WHERE status='running'
   - ``linchpin_orchestrator_tasks``       — in-process orchestrator task dict size
   - ``linchpin_sandbox_containers``       — Docker containers on linchpin-* networks
   - ``linchpin_webhook_delivery_backlog`` — DB: webhook_deliveries pending/failed

DB + Docker gauges are refreshed by a periodic sampler task
(``metrics_sampler_loop``) because prometheus_client ``collect()`` is
synchronous and can't await asyncpg. The in-process gauge reads the
registered ``orchestrator_tasks`` dict at scrape time via ``set_function``.

Every sampler step is best-effort: a failed lookup logs a warning and
keeps the previous value rather than breaking the scrape or the loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from prometheus_client import Gauge

from app.db import fetch_one

logger = logging.getLogger("linchpin-api.metrics")

# Custom gauges (default registry — exposed alongside instrumentator's
# HTTP metrics on /metrics).
ACTIVE_SESSIONS = Gauge(
    "linchpin_active_sessions",
    "Number of sessions currently in status='running'.",
)
ORCHESTRATOR_TASKS = Gauge(
    "linchpin_orchestrator_tasks",
    "In-process orchestrator asyncio tasks per session.",
)
SANDBOX_CONTAINERS = Gauge(
    "linchpin_sandbox_containers",
    "Docker containers attached to any linchpin-* network.",
)
WEBHOOK_BACKLOG = Gauge(
    "linchpin_webhook_delivery_backlog",
    "Webhook deliveries waiting to be sent (status pending/failed).",
)

# Registered by main.lifespan — the live orchestrator_tasks dict on
# app.state. Module-level ref keeps the gauge's set_function sync.
_inprocess_sources: dict[str, dict] = {}


def register_inprocess_sources(orchestrator_tasks: dict) -> None:
    """Point the in-process gauge at the live orchestrator_tasks dict."""
    _inprocess_sources["orchestrator_tasks"] = orchestrator_tasks
    ORCHESTRATOR_TASKS.set_function(
        lambda: len(_inprocess_sources.get("orchestrator_tasks") or {})
    )


def _count_sandbox_containers() -> int:
    """Count Docker containers attached to linchpin-* networks.

    Containers carry no Linchpin labels (docker-py run_kwargs), so
    network membership is the identifying signal. Best-effort: any
    Docker failure counts as 0 rather than raising into the sampler.
    """
    try:
        import docker

        client = docker.from_env()
        containers = client.containers.list(all=True)
        n = 0
        for c in containers:
            networks = ((c.attrs or {}).get("NetworkSettings") or {}).get("Networks") or {}
            if any(
                name.startswith("linchpin-")
                # Atlas B4 — per-env egress proxies are infrastructure,
                # not sandboxes; don't inflate the gauge.
                and not name.startswith("linchpin-egress-")
                for name in networks
            ):
                n += 1
        return n
    except Exception:
        logger.warning("Sandbox container count failed", exc_info=True)
        return 0


async def sample_gauges() -> None:
    """One sampler pass — refresh all DB/Docker gauges, best-effort each."""
    try:
        row = await fetch_one(
            "SELECT COUNT(*) AS n FROM sessions WHERE status = 'running'"
        )
        ACTIVE_SESSIONS.set(row["n"] if row else 0)
    except Exception:
        logger.warning("active_sessions gauge lookup failed", exc_info=True)

    try:
        row = await fetch_one(
            "SELECT COUNT(*) AS n FROM webhook_deliveries "
            "WHERE status IN ('pending', 'failed')"
        )
        WEBHOOK_BACKLOG.set(row["n"] if row else 0)
    except Exception:
        logger.warning("webhook_backlog gauge lookup failed", exc_info=True)

    try:
        n = await asyncio.to_thread(_count_sandbox_containers)
        SANDBOX_CONTAINERS.set(n)
    except Exception:
        logger.warning("sandbox_containers gauge lookup failed", exc_info=True)


async def metrics_sampler_loop() -> None:
    """Periodically refresh DB/Docker-backed gauges.

    Interval via ``LINCHPIN_METRICS_INTERVAL_SEC`` (default 15). Values
    <= 0 disable the loop entirely.
    """
    raw = os.getenv("LINCHPIN_METRICS_INTERVAL_SEC", "15")
    try:
        interval = float(raw)
    except ValueError:
        interval = 15.0
    if interval <= 0:
        logger.info("LINCHPIN_METRICS_INTERVAL_SEC <= 0 — metrics sampler disabled.")
        return

    while True:
        try:
            await sample_gauges()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Metrics sampler pass failed", exc_info=True)
        await asyncio.sleep(interval)


def setup_metrics(app: Any) -> None:
    """Instrument the app: HTTP metrics + /metrics endpoint (no auth)."""
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        excluded_handlers=["health", "metrics"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
