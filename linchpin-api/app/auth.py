"""Authentication & multi-tenant authorization for the Linchpin API.

Two authentication paths:

1. **Bootstrap key** — ``LINCHPIN_API_KEY`` environment variable. Matches are
   mapped to the default tenant (org/project seeded by migration 0020) with
   full scopes (``["*"]``). No database lookup is performed, so the key keeps
   working even before/without the api_keys table.

2. **Issued keys** — rows in the ``api_keys`` table (created via
   ``POST /v1/api-keys``). Looked up by SHA-256 hash with a short-lived
   in-process cache. Each key belongs to exactly one project and carries a
   scope list (e.g. ``["agents:write", "sessions:write"]`` or ``["*"]``).

Used as a FastAPI dependency on all /v1/ routes. The resolved
:class:`AuthContext` is stored on ``request.state.auth`` for downstream
handlers (via :func:`require_auth`).

Authorization model (enforced centrally, zero per-route code):

- ``GET`` / ``HEAD`` / ``OPTIONS`` requests are readable by any valid key.
- Mutating methods require scope ``"{resource}:write"`` (first path segment
  after ``/v1/``) or the wildcard ``"*"``.
- Per-key rate limiting is enforced in-process (Atlas M5, FR-11.2,
  :mod:`app.ratelimit`) — in a multi-replica deployment the in-process
  ceiling is per-replica; precise global limiting belongs at the gateway
  (docs/limits.md). IP allowlisting remains gateway-level.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from app.db import execute, fetch_one, set_tenant_context
from app.ratelimit import check_rate_limit

# Default tenant seeded by migration 0020. Kept in sync with the
# ``workspace_id`` default used by memory stores / skills / dreams.
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_PROJECT_ID = DEFAULT_ORG_ID

# In-process cache TTL for api_keys lookups, keyed by key_hash.
_CACHE_TTL_SECONDS = 60.0
_auth_cache: dict[str, tuple["AuthContext", float]] = {}


@dataclass(frozen=True)
class AuthContext:
    """Resolved identity for a request.

    Attributes:
        project_id: Project the request operates in (tenant boundary).
        org_id: Owning organization of the project.
        api_key_id: ``api_keys.id`` for DB-issued keys; ``None`` for the
            bootstrap environment key.
        scopes: Scope list, e.g. ``("*",)`` or ``("agents:write",)``.
        rate_limit_rpm: Requests-per-minute limit for DB-issued keys
            (Atlas M5, FR-11.2); ``None`` = unlimited (also the bootstrap
            key's value — the ops channel is never limited).
    """

    project_id: str
    org_id: str
    api_key_id: str | None
    scopes: tuple[str, ...] = field(default=())
    rate_limit_rpm: int | None = None

    def has_scope(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes


def _get_api_key() -> str:
    """Return the configured bootstrap API key from the environment."""
    return os.environ.get("LINCHPIN_API_KEY", "")


def _bootstrap_context() -> AuthContext:
    return AuthContext(
        project_id=DEFAULT_PROJECT_ID,
        org_id=DEFAULT_ORG_ID,
        api_key_id=None,
        scopes=("*",),
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _load_api_key(token: str) -> AuthContext | None:
    """Resolve a DB-issued API key to an :class:`AuthContext`.

    Returns ``None`` when the key is unknown or revoked, or when the database
    is unavailable (RuntimeError from the pool) — the caller then rejects the
    request with 401.
    """
    key_hash = _hash_token(token)
    now = time.monotonic()

    cached = _auth_cache.get(key_hash)
    if cached is not None and now - cached[1] < _CACHE_TTL_SECONDS:
        return cached[0]

    try:
        row = await fetch_one(
            """
            SELECT k.id AS api_key_id, k.scopes, k.status, k.rate_limit_rpm,
                   p.id AS project_id, p.org_id
            FROM api_keys k
            JOIN projects p ON p.id = k.project_id
            WHERE k.key_hash = $1
            """,
            key_hash,
        )
        if row is None or row["status"] != "active":
            return None
        ctx = AuthContext(
            project_id=str(row["project_id"]),
            org_id=str(row["org_id"]),
            api_key_id=str(row["api_key_id"]),
            scopes=tuple(row["scopes"] or ()),
            rate_limit_rpm=row["rate_limit_rpm"],
        )
        await execute(
            "UPDATE api_keys SET last_used_at = now() WHERE id = $1",
            row["api_key_id"],
        )
    except RuntimeError:
        # Pool not initialised (e.g. unit tests without a database).
        return None

    _auth_cache[key_hash] = (ctx, now)
    return ctx


def _extract_token(request: Request) -> str:
    """Extract the bearer token, preserving legacy error messages.

    Accepts either:
    - ``Authorization: Bearer <token>`` header (standard)
    - ``?token=<token>`` query parameter (for SSE/EventSource which can't
      send headers)
    """
    # Check query parameter first (for SSE/EventSource)
    query_token = request.query_params.get("token")
    if query_token:
        return query_token

    auth_header = request.headers.get("authorization", "")

    if not auth_header:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Missing Authorization header"},
        )

    parts = auth_header.split(" ", 1)

    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Invalid Authorization header format, expected 'Bearer <token>'"},
        )

    return parts[1]


def _check_scopes(request: Request, ctx: AuthContext) -> None:
    """Centralized scope enforcement (no per-route code needed).

    Read methods are allowed for any valid key; mutating methods require
    ``"{resource}:write"`` or ``"*"``.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    path = request.url.path
    if not path.startswith("/v1/"):
        return
    segments = path[len("/v1/"):].split("/")
    resource = segments[0] if segments and segments[0] else ""
    if not resource:
        return

    required = f"{resource}:write"
    if not ctx.has_scope(required):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": f"missing scope {required}"},
        )


async def verify_bearer_token(request: Request) -> None:
    """FastAPI dependency that validates the bearer token and resolves tenancy.

    Raises ``HTTPException(401)`` when the token is missing, malformed,
    unknown or revoked, and ``HTTPException(403)`` when the key's scopes do
    not cover a mutating request. On success the :class:`AuthContext` is
    stored on ``request.state.auth``.
    """
    token = _extract_token(request)

    api_key = _get_api_key()
    if api_key and token == api_key:
        # Bootstrap key: default tenant, full scopes, no DB round-trip.
        ctx = _bootstrap_context()
    else:
        ctx = await _load_api_key(token)
        if ctx is None:
            raise HTTPException(
                status_code=401,
                detail={"error": "unauthorized", "message": "Invalid API key"},
            )

    # Atlas M5 (FR-11.2) — per-key sliding-window limit. Bootstrap keys
    # (api_key_id None) and keys without a limit pass straight through.
    retry_after = check_rate_limit(ctx.api_key_id, ctx.rate_limit_rpm)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "message": (
                    f"Rate limit exceeded: {ctx.rate_limit_rpm} "
                    "requests per minute"
                ),
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    _check_scopes(request, ctx)
    request.state.auth = ctx
    # Atlas M6 (C1 phase-2) — pin the tenant for the RLS policies
    # (migration 0030). The query helpers mirror this contextvar onto the
    # pooled connection as the ``app.current_project`` GUC; background
    # tasks never set it and run fail-open. The _load_api_key lookup above
    # already ran with an unset GUC, so pre-auth queries are unaffected.
    set_tenant_context(ctx.project_id)


def require_auth(request: Request) -> AuthContext:
    """Dependency returning the resolved :class:`AuthContext` for a route.

    ``verify_bearer_token`` (router-level dependency) always runs first and
    populates ``request.state.auth``; the check below is purely defensive.
    """
    ctx = getattr(request.state, "auth", None)
    if ctx is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Missing Authorization header"},
        )
    return ctx
