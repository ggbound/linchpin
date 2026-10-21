"""Linchpin API — FastAPI service for managed AI agents."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Configure root logger so orchestrator and other module logs are visible
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

from app.auth import verify_bearer_token
# Atlas C2 — replica identity, session-lease event bus
from app import cluster
from app.db import close_pool, create_pool
from app.deployments import (
    deployments_scheduler_loop,
    scheduler_enabled as deployments_scheduler_enabled,
)
from app.idempotency import cleanup_loop as idempotency_cleanup_loop
from app.metrics import (
    metrics_sampler_loop,
    register_inprocess_sources,
    setup_metrics,
)
from app.migrations import check_migrations_current
from app.orchestrator import (
    cleanup_expired_sessions,
    recover_sessions,
    supervise_stale_sessions,
    supervisor_enabled as lease_supervisor_enabled,
)
from app.retention import cleanup_loop as events_retention_cleanup_loop
from app.routes.admin import router as admin_router
from app.routes.agents import router as agents_router
from app.routes.artifacts import router as artifacts_router
from app.routes.audit import router as audit_router
from app.routes.deployments import router as deployments_router
from app.routes.environments import router as environments_router
from app.routes.executor_nodes import router as executor_nodes_router
from app.routes.executor_nodes import daemon_router as executor_daemon_router
from app.sandbox_resolver import resolve_session_sandbox
from app.routes.files import router as files_router
from app.routes.memories import router as memories_router
from app.routes.memory_stores import router as memory_stores_router
from app.routes.memory_versions import router as memory_versions_router
from app.routes.dreams import router as dreams_router
from app.routes.session_resources import router as session_resources_router
from app.routes.sessions import router as sessions_router
from app.routes.skills import router as skills_router
from app.routes.usage import router as usage_router
from app.routes.vaults import router as vaults_router
from app.routes.webhooks import router as webhooks_router
from app.sandbox import (
    DockerSandbox,
    SandboxError,
    create_sandbox,
    ensure_docker_networks,
)
from app.tracing import setup_tracing, shutdown_tracing
from app.watcher import watch_session_deliverables
from app.webhooks import delivery_worker_loop, worker_enabled

logger = logging.getLogger("linchpin-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Startup hooks:
    - Run Alembic migration check
    - Create asyncpg database connection pool
    - Pre-create Docker networks (linchpin-none, linchpin-open)
    - Recover non-terminal sessions

    Shutdown hooks:
    - Close database connection pool
    """
    logger.info("Starting linchpin-api...")

    # Atlas B10 — OpenTelemetry tracing for model requests (no-op unless
    # LINCHPIN_OTEL_ENABLED). Setup failure must not block startup.
    setup_tracing()

    # Verify database migrations are up to date
    check_migrations_current()

    # Create asyncpg database connection pool
    await create_pool()

    # Atlas C2 — replica identity + shared-channel event bus. The identity
    # stamps sessions.orchestrator_instance for lease bookkeeping; the bus
    # carries all LISTEN/NOTIFY traffic on one connection per process.
    logger.info("Instance id: %s", cluster.get_instance_id())
    await cluster.start_event_bus()

    # Pre-create Docker networks (linchpin-none, linchpin-open)
    # LINCHPIN_SKIP_DOCKER=true — local-debugging escape hatch for hosts
    # without a Docker daemon: skips all Docker wiring. Session launch and
    # container lifecycle will fail, but metadata/admin endpoints work.
    skip_docker = os.environ.get("LINCHPIN_SKIP_DOCKER", "false").lower() == "true"
    if skip_docker:
        logger.warning(
            "LINCHPIN_SKIP_DOCKER=true — Docker sandbox disabled; "
            "session launch and container lifecycle are unavailable."
        )
        app.state.sandbox = None
        app.state.ttl_cleanup_task = None
    else:
        await ensure_docker_networks()

        # Store sandbox instance on app.state for route access
        app.state.sandbox = DockerSandbox()

    # Atlas C3 — optional microVM runtime for environments that declare
    # isolation: "microvm". Off by default (LINCHPIN_ENABLE_MICROVM);
    # a missing driver SDK degrades to a warning so docker-isolated
    # sessions keep working. Kept out of the skip_docker branch guard —
    # microVM provisioning does not depend on the local Docker daemon.
    app.state.microvm_sandbox = None
    if os.environ.get("LINCHPIN_ENABLE_MICROVM", "false").lower() == "true":
        try:
            app.state.microvm_sandbox = create_sandbox("microvm")
            logger.info("MicroVM sandbox runtime enabled (E2B driver).")
        except SandboxError as exc:
            logger.warning("MicroVM sandbox disabled: %s", exc)

    # Track orchestrator async tasks per session so they can be cancelled
    app.state.orchestrator_tasks: dict[str, "asyncio.Task"] = {}

    # Atlas B10 — point the linchpin_orchestrator_tasks gauge at the live
    # dict so scrapes see the in-process task count.
    register_inprocess_sources(app.state.orchestrator_tasks)

    # Track deliverables-watcher tasks per session (PR5 D3). The dict is
    # initialized here (not lazily in create_session) so recover_sessions
    # can populate it for sessions that survived a process restart — that
    # is the wiring that makes the D4 "boot scan on startup" claim true.
    app.state.watcher_tasks: dict[str, "asyncio.Task"] = {}

    # Start background TTL cleanup task (Docker container expiry — skipped
    # together with the sandbox under LINCHPIN_SKIP_DOCKER)
    if not skip_docker:
        ttl_task = asyncio.create_task(
            cleanup_expired_sessions(
                app.state.sandbox,
                app.state.orchestrator_tasks,
                sandbox_resolver=resolve_session_sandbox,
            )
        )
        app.state.ttl_cleanup_task = ttl_task

    # v0.3 PR5 — memory version GC task. Hourly by default; controlled
    # by LINCHPIN_MEMORY_GC_INTERVAL_SEC. Skipped when LINCHPIN_MEMORY_GC=false
    # for test/maintenance windows that don't want the cron tick at all.
    if os.environ.get("LINCHPIN_MEMORY_GC", "true").lower() != "false":
        from app.memory import cleanup_expired_memory_versions
        app.state.memory_gc_task = asyncio.create_task(
            cleanup_expired_memory_versions()
        )
    else:
        app.state.memory_gc_task = None

    # v0.2.0 item #14 — webhook delivery worker. One per process; uses
    # SELECT … FOR UPDATE SKIP LOCKED so multi-process deployments are
    # safe out of the box. ``LINCHPIN_WEBHOOKS_WORKER=false`` skips it
    # entirely (used by tests so the worker doesn't fire HTTP calls).
    if worker_enabled():
        app.state.webhook_worker_task = asyncio.create_task(delivery_worker_loop())
    else:
        app.state.webhook_worker_task = None

    # Atlas A8 — idempotency-key TTL cleanup. Hourly pass deletes rows
    # older than 24h.
    app.state.idempotency_cleanup_task = asyncio.create_task(
        idempotency_cleanup_loop()
    )

    # Atlas B12 — events retention cleanup. Hourly pass deletes events
    # older than LINCHPIN_EVENTS_RETENTION_DAYS (default 90; <=0 disables).
    app.state.events_retention_task = asyncio.create_task(
        events_retention_cleanup_loop()
    )

    # Atlas B10 — periodic refresh of the DB/Docker-backed gauges.
    # LINCHPIN_METRICS_SAMPLER=false skips it (tests).
    if os.environ.get("LINCHPIN_METRICS_SAMPLER", "true").lower() != "false":
        app.state.metrics_sampler_task = asyncio.create_task(metrics_sampler_loop())
    else:
        app.state.metrics_sampler_task = None

    # Atlas B7 — deployment scheduler. One per process; claims due rows
    # with SELECT … FOR UPDATE SKIP LOCKED so multi-process deployments
    # are safe out of the box. ``LINCHPIN_DEPLOYMENTS_SCHEDULER=false``
    # skips it entirely (tests).
    if deployments_scheduler_enabled():
        app.state.deployments_scheduler_task = asyncio.create_task(
            deployments_scheduler_loop(app)
        )
    else:
        app.state.deployments_scheduler_task = None

    # Atlas C2 — lease supervisor: periodically reclaims sessions whose
    # owning replica stopped heartbeating (crash / network partition).
    # Takeover races are settled by the advisory-lock try-acquire, so
    # multi-replica deployments are safe. Skipped under LINCHPIN_SKIP_DOCKER
    # (no containers to take over) or LINCHPIN_LEASE_SUPERVISOR=false.
    if not skip_docker and lease_supervisor_enabled():
        app.state.lease_supervisor_task = asyncio.create_task(
            supervise_stale_sessions(
                app.state.sandbox,
                app.state.orchestrator_tasks,
                watcher_tasks=app.state.watcher_tasks,
                spawn_watcher=watch_session_deliverables,
                sandbox_resolver=resolve_session_sandbox,
            )
        )
    else:
        app.state.lease_supervisor_task = None

    # Recover non-terminal sessions. PR5 — pass watcher_tasks + the
    # watcher spawner so any session with a surviving container gets its
    # deliverables watcher (and the boot scan inside it) re-attached.
    # Skipped under LINCHPIN_SKIP_DOCKER (no containers to recover).
    if not skip_docker:
        await recover_sessions(
            app.state.sandbox,
            app.state.orchestrator_tasks,
            watcher_tasks=app.state.watcher_tasks,
            spawn_watcher=watch_session_deliverables,
            sandbox_resolver=resolve_session_sandbox,
        )

    # Validate VAULT_ENCRYPTION_KEY early (warn, don't crash — key only
    # required when vault features are actually used)
    try:
        from app.encryption import get_encryption_service
        get_encryption_service()
        logger.info("VAULT_ENCRYPTION_KEY validated successfully.")
    except RuntimeError as exc:
        logger.warning("VAULT_ENCRYPTION_KEY not configured: %s — vault features will be unavailable", exc)

    logger.info("linchpin-api started.")
    yield

    # Shutdown
    logger.info("Shutting down linchpin-api...")

    # Cancel TTL cleanup task
    ttl_task = getattr(app.state, "ttl_cleanup_task", None)
    if ttl_task is not None:
        ttl_task.cancel()
        try:
            await ttl_task
        except asyncio.CancelledError:
            pass

    # Cancel webhook delivery worker if running
    wht = getattr(app.state, "webhook_worker_task", None)
    if wht is not None:
        wht.cancel()
        try:
            await wht
        except (asyncio.CancelledError, Exception):
            pass

    # Atlas A8 — cancel idempotency cleanup task
    ict = getattr(app.state, "idempotency_cleanup_task", None)
    if ict is not None:
        ict.cancel()
        try:
            await ict
        except (asyncio.CancelledError, Exception):
            pass

    # Atlas B12 — cancel events retention cleanup task
    ert = getattr(app.state, "events_retention_task", None)
    if ert is not None:
        ert.cancel()
        try:
            await ert
        except (asyncio.CancelledError, Exception):
            pass

    # Atlas B10 — cancel metrics sampler task
    mst = getattr(app.state, "metrics_sampler_task", None)
    if mst is not None:
        mst.cancel()
        try:
            await mst
        except (asyncio.CancelledError, Exception):
            pass

    # Atlas B7 — cancel deployment scheduler task
    dst = getattr(app.state, "deployments_scheduler_task", None)
    if dst is not None:
        dst.cancel()
        try:
            await dst
        except (asyncio.CancelledError, Exception):
            pass

    # v0.3 PR5 — cancel memory GC task if running.
    mgt = getattr(app.state, "memory_gc_task", None)
    if mgt is not None:
        mgt.cancel()
        try:
            await mgt
        except (asyncio.CancelledError, Exception):
            pass

    # Atlas C2 — cancel lease supervisor task
    lst = getattr(app.state, "lease_supervisor_task", None)
    if lst is not None:
        lst.cancel()
        try:
            await lst
        except (asyncio.CancelledError, Exception):
            pass

    # Atlas C2 — stop the shared-channel event bus before the pool closes.
    await cluster.stop_event_bus()

    # Cancel any in-flight deliverables-watcher tasks (PR5). Boot scan on
    # the next start re-ingests anything written between now and restart.
    for sid, w in list(app.state.watcher_tasks.items()):
        if not w.done():
            w.cancel()
    for sid, w in list(app.state.watcher_tasks.items()):
        try:
            await w
        except (asyncio.CancelledError, Exception):
            pass
    app.state.watcher_tasks.clear()

    # Close database connection pool
    await close_pool()

    # Atlas B10 — flush and stop OTel span export (no-op when disabled).
    shutdown_tracing()

    logger.info("linchpin-api shut down.")


app = FastAPI(
    title="Linchpin API",
    version="0.7.0",
    description="Self-hostable runtime for managed AI agents",
    lifespan=lifespan,
)

# CORS middleware — allow the console origin (and any custom origins)
cors_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Linchpin-API-Version"],
    expose_headers=["Linchpin-API-Version", "Linchpin-Deprecation"],
)


# Atlas A5 — unified error envelope (PRD §6.4). Every 4xx/5xx response
# body is normalized to {"error": <code>, "message": ..., "detail": ...}.
# Routes that already raise detail={"error": ..., "message": ...} keep
# their explicit codes; anything else derives a code from the status.
_STATUS_ERROR_CODES: dict[int, str] = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "invalid_request",
    429: "rate_limited",
}


def _error_code(status_code: int) -> str:
    if status_code in _STATUS_ERROR_CODES:
        return _STATUS_ERROR_CODES[status_code]
    return "invalid_request" if status_code < 500 else "internal"


def _error_envelope(status_code: int, detail: Any) -> dict:
    if isinstance(detail, dict) and ("error" in detail or "message" in detail):
        extra = {k: v for k, v in detail.items() if k not in ("error", "message")}
        return {
            "error": detail.get("error") or _error_code(status_code),
            "message": detail.get("message") or detail.get("error") or "Request failed",
            "detail": extra or None,
        }
    return {
        "error": _error_code(status_code),
        "message": detail if isinstance(detail, str) else "Request failed",
        "detail": detail if not isinstance(detail, (str, type(None))) else None,
    }


@app.exception_handler(StarletteHTTPException)
async def unified_http_exception_handler(request, exc):  # type: ignore[no-untyped-def]
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_envelope(exc.status_code, exc.detail),
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def unified_validation_exception_handler(request, exc):  # type: ignore[no-untyped-def]
    # Drop ctx/url so the payload is always JSON-serializable (ctx can
    # carry exception instances from Pydantic validators).
    errors = [
        {k: v for k, v in err.items() if k not in ("ctx", "url")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=_error_envelope(422, {
            "error": "invalid_request",
            "message": "Request validation failed",
            "errors": errors,
        }),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):  # type: ignore[no-untyped-def]
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=_error_envelope(500, None),
    )


# v0.2.0 breaking bundle — middleware that resolves the
# `Linchpin-API-Version` header on every request, caches it on
# request.state, and echoes the resolved version back on the response.
# An unknown version short-circuits to 400 with the supported list,
# matching the behavior negotiate() would produce from a dependency.
@app.middleware("http")
async def linchpin_api_version_middleware(request, call_next):  # type: ignore[no-untyped-def]
    from app import api_version as _av
    try:
        version = _av.negotiate(request)
    except StarletteHTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_envelope(exc.status_code, exc.detail),
        )
    response = await call_next(request)
    _av.echo_version_header(response, version)
    return response


# Router for all /v1/ endpoints — auth required
v1_router = APIRouter(prefix="/v1", dependencies=[Depends(verify_bearer_token)])

v1_router.include_router(admin_router)
v1_router.include_router(audit_router)
v1_router.include_router(agents_router)
v1_router.include_router(artifacts_router)
v1_router.include_router(deployments_router)
v1_router.include_router(environments_router)
# Atlas M4 (FR-4.6) — executor node management lives on the authenticated
# v1 surface; the daemon heartbeat is mounted separately (app level) since
# node tokens are not API keys and must bypass verify_bearer_token.
v1_router.include_router(executor_nodes_router)
app.include_router(executor_daemon_router)
v1_router.include_router(files_router)
v1_router.include_router(memory_stores_router)
v1_router.include_router(memories_router)
v1_router.include_router(memory_versions_router)
v1_router.include_router(sessions_router)
v1_router.include_router(session_resources_router)
v1_router.include_router(dreams_router)
v1_router.include_router(skills_router)
v1_router.include_router(usage_router)
v1_router.include_router(vaults_router)
v1_router.include_router(webhooks_router)

app.include_router(v1_router)


# Atlas B10 — HTTP metrics + /metrics scrape endpoint (root app, no auth)
setup_metrics(app)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


# Atlas M5 (FR-12.2) — custom OpenAPI 3.x document. FastAPI's default
# generation knows nothing about the bearer-key auth (it is enforced by a
# router-level dependency, not per-operation security) or the unified error
# envelope, so the schema is post-processed: a bearerAuth security scheme,
# standardized 401/429/500 responses referencing a reusable ErrorEnvelope
# component, and tag metadata. Only the /v1/ surface is annotated — the
# executor-daemon heartbeat also matches the /v1/ prefix but authenticates
# with node tokens, so it is excluded explicitly.
_OPENAPI_TAGS = [
    {"name": "admin", "description": "Projects, API keys and tenant quotas"},
    {"name": "agents", "description": "Agent definitions"},
    {"name": "artifacts", "description": "Session deliverable artifacts"},
    {"name": "audit", "description": "Audit event log"},
    {"name": "deployments", "description": "Scheduled agent deployments"},
    {"name": "dreams", "description": "Memory consolidation runs"},
    {"name": "environments", "description": "Sandbox environments"},
    {"name": "executors", "description": "BYO executor nodes (FR-4.6)"},
    {"name": "files", "description": "File uploads and downloads"},
    {"name": "memory", "description": "Memory stores, memories and versions"},
    {"name": "session_resources", "description": "Per-session resource bindings"},
    {"name": "sessions", "description": "Agent sessions, messages and events"},
    {"name": "skills", "description": "Agent skills"},
    {"name": "usage", "description": "Token usage aggregation"},
    {"name": "vaults", "description": "Vaults and credentials"},
    {"name": "webhooks", "description": "Webhook endpoints and deliveries"},
]

# Authenticated with an executor-node token, not an API key.
_OPENAPI_NODE_AUTH_PATHS = {
    "/v1/executor-nodes/{node_id}/heartbeat",
}

# Operations that can additionally fail with a *project quota* 429
# (concurrent-session cap / monthly token budget), not just per-key
# rate limiting.
_OPENAPI_QUOTA_OPERATIONS = {
    "post /v1/sessions",
    "post /v1/sessions/{parent_id}/threads",
}

_AUTH_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    from fastapi.openapi.utils import get_openapi

    from app.models import ErrorEnvelope

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})

    components["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "API key — the bootstrap ``LINCHPIN_API_KEY`` or a key "
                "issued via ``POST /v1/api-keys``. SSE endpoints also "
                "accept it as a ``?token=`` query parameter."
            ),
        }
    }

    def _error_response(description: str) -> dict:
        return {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                }
            },
        }

    components["schemas"]["ErrorEnvelope"] = ErrorEnvelope.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    components["responses"] = {
        "Unauthorized": _error_response(
            "Missing, malformed, unknown or revoked API key"
        ),
        "RateLimited": _error_response(
            "Per-key request rate exceeded; Retry-After carries the wait in seconds"
        ),
        "QuotaExceeded": _error_response(
            "Project concurrent-session cap or monthly token budget reached "
            "(error code quota_exceeded)"
        ),
        "Internal": _error_response("Unexpected server error"),
    }

    for path, operations in schema.get("paths", {}).items():
        if not path.startswith("/v1/") or path in _OPENAPI_NODE_AUTH_PATHS:
            continue
        for method, op in operations.items():
            if method not in _AUTH_HTTP_METHODS:
                continue
            op.setdefault("security", [{"bearerAuth": []}])
            responses = op.setdefault("responses", {})
            responses.setdefault(
                "401",
                _error_response("Missing, malformed, unknown or revoked API key"),
            )
            if f"{method} {path}" in _OPENAPI_QUOTA_OPERATIONS:
                responses.setdefault(
                    "429",
                    _error_response(
                        "Project concurrent-session cap or monthly token "
                        "budget reached (error code quota_exceeded)"
                    ),
                )
            else:
                responses.setdefault(
                    "429",
                    _error_response(
                        "Per-key request rate exceeded; Retry-After carries "
                        "the wait in seconds"
                    ),
                )
            responses.setdefault(
                "500", _error_response("Unexpected server error")
            )

    schema["tags"] = _OPENAPI_TAGS
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
