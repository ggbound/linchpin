"""Session CRUD endpoints.

POST   /v1/sessions              — Create session
GET    /v1/sessions              — List sessions (paginated, optional agent_id filter)
GET    /v1/sessions/{id}         — Get session by id with stats and usage
POST   /v1/sessions/{id}         — Update session title/metadata
DELETE /v1/sessions/{id}         — Terminate session (set status=terminated, destroy container)
POST   /v1/sessions/{id}/archive — Archive session (set archived_at)
GET    /v1/sessions/{id}/filesystem       — List a directory inside the sandbox (Atlas B5, FR-9.2)
GET    /v1/sessions/{id}/filesystem/raw   — Download a file from the sandbox as bytes (Atlas B5, FR-9.2)

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.6, 6.5, 19.2, 19.3, 20.1, 20.2, 20.3, FR-9.2
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import shutil
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.audit import record_audit
from app.auth import AuthContext, require_auth
from app.db import fetch_all, fetch_one, execute, listen, notify
from app.event_crypto import (
    cache_session_data_key,
    drop_session_data_key,
    generate_data_key,
    wrap_data_key,
)
from app.events import (
    append_event,
    decode_cursor,
    encode_cursor,
    get_events,
    release_session_event_lock,
)
from app.files import get_file_store
# Atlas B2 — in-process interrupt flag, set when a user.interrupt lands.
from app import interrupts
from app.idempotency import (
    IdempotencyHashMismatch,
    compute_request_hash,
    record as idempotency_record,
    replay as idempotency_replay,
)
from app.memory import (
    materialize_memory_store,
    max_stores_per_session,
    session_memory_cache_dir,
)
from app.git_repo import GitCloneError, clone_or_update
from app.memory_watcher import watch_memory_store
from app.encryption import get_encryption_service
from app.orchestrator import run_session, transition
from app.egress import proxy_env_from_networking
from app.quotas import enforce_session_quotas
from app.sandbox import (
    DEFAULT_BASE_IMAGE,
    ResourceMount,
    SandboxError,
    runtime_for_config,
)
from app.sandbox_resolver import (
    resolve_sandbox_for_config,
    resolve_session_sandbox,
)
from app.streaming import get_stream
from app.watcher import ensure_session_outputs_dir, session_outputs_dir, watch_session_deliverables
from app.models import (
    CreateSessionRequest,
    EnvironmentPackages,
    EventResponse,
    FileResource,
    MemoryStoreResource,
    PaginatedEventsResponse,
    PaginatedListResponse,
    PostEventsRequest,
    SandboxResources,
    SessionResource,
    SessionResponse,
    SessionStats,
    SessionUsage,
    ToolSearchConfig,
    GitRepositoryResource,
    GithubRepositoryResource,
    OutcomeDefinition,
    OutcomeEvaluation,
    OutcomeEvaluationsResponse,
    VaultResource,
)
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("linchpin-api.sessions")

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _row_to_resource(row) -> SessionResource:
    """Convert a session_resources asyncpg Record to a SessionResource."""
    config_raw = row["config"]
    config = json.loads(config_raw) if isinstance(config_raw, str) else config_raw
    return SessionResource(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        type=row["type"],
        mount_path=row["mount_path"],
        config=config,
        state=row["state"],
        error=row["error"],
        created_at=row["created_at"],
        unmounted_at=row["unmounted_at"],
    )


def _row_to_session(row, resources: list[SessionResource] | None = None) -> SessionResponse:
    """Convert an asyncpg Record to a SessionResponse."""
    stats_raw = row["stats"]
    usage_raw = row["usage"]
    metadata_raw = row["metadata"]

    stats = json.loads(stats_raw) if isinstance(stats_raw, str) else stats_raw
    usage = json.loads(usage_raw) if isinstance(usage_raw, str) else usage_raw
    metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw

    # Parse vault_ids — may be a JSON string, list, or missing
    vault_ids_raw = row.get("vault_ids", [])
    if vault_ids_raw is None:
        vault_ids = []
    elif isinstance(vault_ids_raw, str):
        vault_ids = json.loads(vault_ids_raw)
    else:
        vault_ids = vault_ids_raw

    # v0.6 — outcome + parent_session_id. ``outcome`` is JSONB and may
    # arrive as a dict or a JSON string; both are tolerated. Rows from
    # pre-0013 schemas (no column) come back without the key.
    # asyncpg Record.keys() is a one-shot iterator — set() it first.
    keys = set(row.keys())
    outcome_raw = row["outcome"] if "outcome" in keys else None
    if isinstance(outcome_raw, str):
        outcome_raw = json.loads(outcome_raw) if outcome_raw else None
    outcome = (
        OutcomeDefinition.model_validate(outcome_raw)
        if outcome_raw is not None else None
    )
    parent_session_id = (
        str(row["parent_session_id"])
        if "parent_session_id" in keys and row["parent_session_id"] is not None
        else None
    )

    # A2 — stop_reason / retry_count (0016). ``row.get`` tolerates rows
    # from pre-0016 schemas that lack the columns.
    stop_reason = row.get("stop_reason")
    retry_count = int(row.get("retry_count") or 0)

    # B8 — event encryption flag (0023). Rows from pre-0023 schemas lack
    # the column; treat them as plaintext.
    events_encrypted = bool(row.get("events_encrypted", False))

    # B7 — deployment provenance (0025). Rows from pre-0025 schemas lack
    # the column.
    deployment_id = (
        str(row["deployment_id"])
        if "deployment_id" in keys and row["deployment_id"] is not None
        else None
    )

    return SessionResponse(
        id=str(row["id"]),
        agent_id=str(row["agent_id"]),
        agent_version=row["agent_version"],
        environment_id=str(row["environment_id"]),
        status=row["status"],
        stop_reason=stop_reason,
        retry_count=retry_count,
        container_id=row["container_id"],
        title=row["title"],
        metadata=metadata,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archived_at=row["archived_at"],
        last_event_cursor=row["last_event_cursor"],
        ttl_seconds=row["ttl_seconds"],
        stats=SessionStats(**stats),
        usage=SessionUsage(**usage),
        vault_ids=vault_ids,
        resources=resources or [],
        outcome=outcome,
        parent_session_id=parent_session_id,
        deployment_id=deployment_id,
        events_encrypted=events_encrypted,
    )


async def _mark_provisioning_failed(session_id: str, message: str, code: str) -> None:
    """Atlas A2 — record a pre-container provisioning failure on the row.

    The session row is persisted (status='provisioning') before the image
    build / container create runs; when either fails, flip the row to
    error('provisioning_failed') + emit session.error so the failure is
    queryable, then the caller surfaces its 500 as before. Best-effort: a
    bookkeeping failure here must not mask the original SandboxError.
    """
    try:
        await transition(session_id, "error", stop_reason="provisioning_failed")
        await append_event(
            session_id,
            "session.error",
            {"error": message, "code": code, "source": "provisioning"},
        )
    except Exception:
        logger.exception(
            "failed to mark session %s provisioning_failed (%s)", session_id, code
        )


async def _load_session_resources(session_id: uuid.UUID) -> list[SessionResource]:
    """Load all session_resources rows for a session (any state)."""
    rows = await fetch_all(
        """SELECT * FROM session_resources
           WHERE session_id = $1
           ORDER BY created_at ASC""",
        session_id,
    )
    return [_row_to_resource(r) for r in rows]


def _network_for_environment(config: dict) -> str:
    """Determine the Docker network name from an environment config dict.

    v0.2.0 item #4 — `limited` mode maps to `linchpin-limited`. In v0.2.0
    that network is an internal bridge (same as `linchpin-none`); v0.2.x
    will add the egress proxy that turns the env's `allowed_hosts` /
    `allow_mcp_servers` / `allow_package_managers` lists into actual
    permitted traffic.
    """
    networking = config.get("networking", {})
    net_type = networking.get("type", "none")
    if net_type == "unrestricted":
        return "linchpin-open"
    if net_type == "limited":
        return "linchpin-limited"
    return "linchpin-none"


async def _resolve_kek_credential(
    credential_id: str,
    vault_ids: list[str],
) -> str:
    """Resolve the customer KEK for BYOK session encryption (Atlas M6).

    The credential must be a live ``byok_kek`` credential whose vault the
    caller listed in ``vault_ids`` (the customer's declaration that the
    session may use that vault). Returns the plaintext KEK; anything off
    raises 404/422 before the session row is inserted.
    """
    try:
        cred_uid = uuid.UUID(credential_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": f"x-kek-credential-id {credential_id!r} is not a valid credential id",
            },
        )
    row = await fetch_one(
        """
        SELECT id, vault_id, encrypted_secrets FROM credentials
        WHERE id = $1 AND credential_type = 'byok_kek'
          AND archived_at IS NULL
        """,
        cred_uid,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": (
                    f"byok_kek credential {credential_id} not found "
                    "(or archived)"
                ),
            },
        )
    if str(row["vault_id"]) not in vault_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": (
                    f"KEK credential {credential_id} belongs to vault "
                    f"{row['vault_id']}, which is not in the session's "
                    "vault_ids"
                ),
            },
        )
    kek = json.loads(
        get_encryption_service().decrypt(row["encrypted_secrets"])
    )["kek"]
    return kek


@router.post("", status_code=201, response_model=SessionResponse)
async def create_session(
    body: CreateSessionRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    events_encrypted: bool = Header(
        default=False, alias="x-events-encrypted"
    ),
    kek_credential_id: str | None = Header(
        default=None, alias="x-kek-credential-id"
    ),
    ctx: AuthContext = Depends(require_auth),
) -> SessionResponse:
    """Create a new session: validate refs, provision container, insert record.

    Atlas A8 — an ``Idempotency-Key`` header makes the create exactly-once:
    a retry with the same key + same body replays the stored response.
    Atlas B8 — ``x-events-encrypted: true`` enables envelope encryption of
    business-content event payloads for this session (FR-10.3).
    Atlas M6 BYOK — ``x-kek-credential-id`` names a ``byok_kek`` vault
    credential (one of the session's ``vault_ids``); when present together
    with ``x-events-encrypted: true`` the per-session data key is wrapped
    under the customer's KEK instead of the platform master key.
    """
    # Atlas A8 — replay a completed response before any side effect.
    idem_endpoint = "POST /v1/sessions"
    idem_hash = compute_request_hash("POST", "/v1/sessions", body.model_dump(mode="json"))
    if idempotency_key:
        try:
            replayed = await idempotency_replay(idempotency_key, idem_endpoint, idem_hash)
        except IdempotencyHashMismatch:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "conflict",
                    "message": (
                        f"Idempotency-Key {idempotency_key!r} was already used "
                        "with a different request body"
                    ),
                },
            )
        if replayed is not None:
            replay_status, replay_body = replayed
            return JSONResponse(status_code=replay_status, content=replay_body)

    # C1 — tenant boundary: every referenced resource must belong to the
    # caller's project; the session row inherits the project.
    project_uid = uuid.UUID(ctx.project_id)

    # Atlas M5 (FR-11.2/11.3) — project quotas before any side effect.
    await enforce_session_quotas(ctx.project_id)

    # Validate agent_id exists
    try:
        agent_uid = uuid.UUID(body.agent_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Agent {body.agent_id} not found"},
        )

    agent_row = await fetch_one(
        "SELECT * FROM agents WHERE id = $1 AND project_id = $2",
        agent_uid,
        project_uid,
    )
    if agent_row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Agent {body.agent_id} not found"},
        )

    # Validate environment_id exists
    try:
        env_uid = uuid.UUID(body.environment_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Environment {body.environment_id} not found"},
        )

    env_row = await fetch_one(
        "SELECT * FROM environments WHERE id = $1 AND project_id = $2",
        env_uid,
        project_uid,
    )
    if env_row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Environment {body.environment_id} not found"},
        )

    # v0.3 PR6 — vault_ids → resources[] fold-in. Accept both shapes:
    # legacy ``vault_ids: [...]`` (deprecated) and ``resources: [{type:
    # vault, vault_id: ...}]`` (new). Normalize to a single deduped list
    # for validation and persistence. The original ``body.vault_ids``
    # flag drives the deprecation signaling below; the union drives the
    # vault existence + archive check.
    legacy_vault_ids_supplied = bool(body.vault_ids)
    vault_resources_from_resources = [
        r.vault_id for r in body.resources if isinstance(r, VaultResource)
    ]
    seen: set[str] = set()
    unified_vault_ids: list[str] = []
    for vid in list(body.vault_ids) + vault_resources_from_resources:
        if vid in seen:
            continue
        seen.add(vid)
        unified_vault_ids.append(vid)

    # Validate vault_ids: each must exist and not be archived
    if unified_vault_ids:
        for vid in unified_vault_ids:
            try:
                vault_uid = uuid.UUID(vid)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"Invalid vault id: {vid}",
                    },
                )
            vault_row = await fetch_one(
                "SELECT id, archived_at FROM vaults WHERE id = $1 AND project_id = $2",
                vault_uid,
                project_uid,
            )
            if vault_row is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"Vault {vid} not found",
                    },
                )
            if vault_row["archived_at"] is not None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"Vault {vid} is archived",
                    },
                )

    # Validate resources[] — v0.3 adds memory_store dispatch. `file` and
    # `memory_store` are accepted; `github_repository` still 501s. File
    # resources must reference an existing, unarchived upload (not a
    # deliverable — re-mounting deliverables across sessions would be a covert
    # channel; explicit re-upload is required).
    #
    # PR3 closes PR2's TOCTOU note for files: the upfront validation block here
    # checks existence + source + storage_path. The mount-time re-check below
    # catches the narrow race between this block and container creation (file
    # deleted between the two) and transitions the resource to state='failed'
    # with a session.resource_mount_failed event — the spec's "session still
    # starts" path.
    #
    # Memory stores are looked up here too; failures (missing/archived store,
    # cap exceeded, duplicate store_id in the same request) fail the whole
    # create — they're not deferrable the way a missing file is, because we
    # need the store row to materialize the host cache and to emit the system
    # prompt block before the agent runs.
    file_meta: list[dict[str, Any] | None] = []   # parallel; None for non-file
    memory_meta: list[dict[str, Any] | None] = [] # parallel; None for non-memory
    seen_memory_store_ids: set[str] = set()
    seen_upload_mounts: dict[str, str] = {}  # default mount_path -> file_id (Atlas B5)
    for resource in body.resources:
        if isinstance(resource, FileResource):
            try:
                file_uid = uuid.UUID(resource.file_id)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"Invalid file id: {resource.file_id}",
                    },
                )
            file_row = await fetch_one(
                "SELECT id, source, archived_at, storage_path, filename "
                "FROM files WHERE id = $1 AND project_id = $2",
                file_uid,
                project_uid,
            )
            if file_row is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"File {resource.file_id} not found",
                    },
                )
            if file_row["archived_at"] is not None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"File {resource.file_id} is archived",
                    },
                )
            if file_row["source"] != "upload":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": (
                            f"File {resource.file_id} has source='{file_row['source']}'; "
                            "only uploads can be mounted as session resources"
                        ),
                    },
                )
            # Atlas B5 — resolve the container-side mount path. Explicit
            # mount_path values already passed the model-level validator
            # (reserved prefixes, traversal, uniqueness across resources).
            # When omitted, default to /mnt/session/uploads/<basename> so
            # uploads are visible in the sandbox without client-side path
            # bookkeeping. Default paths bypass the model validator (they're
            # synthesized here, not carried by the request), so duplicate
            # defaults are rejected explicitly below.
            resource_mount_path = resource.mount_path
            if resource_mount_path is None:
                raw_name = file_row["filename"] or "upload"
                basename = str(raw_name).replace("\\", "/").split("/")[-1].strip()
                if not basename or basename in (".", ".."):
                    basename = "upload"
                resource_mount_path = f"/mnt/session/uploads/{basename}"
                prior = seen_upload_mounts.get(resource_mount_path)
                if prior is not None:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": "validation_error",
                            "message": (
                                f"files {prior} and {resource.file_id} would both "
                                f"default-mount at {resource_mount_path!r}; pass "
                                "explicit mount_path values to disambiguate"
                            ),
                        },
                    )
                seen_upload_mounts[resource_mount_path] = resource.file_id
            file_meta.append({
                "file_uid": file_uid,
                "storage_path": file_row["storage_path"],
                "filename": file_row["filename"],
                "mount_path": resource_mount_path,
            })
            memory_meta.append(None)
        elif isinstance(resource, MemoryStoreResource):
            try:
                store_uid = uuid.UUID(resource.memory_store_id)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"Invalid memory_store id: {resource.memory_store_id}",
                    },
                )
            # Dedupe by canonical UUID form so callers can't bypass the check
            # via dashes/case/braces variations on the same id.
            canonical_id = str(store_uid)
            if canonical_id in seen_memory_store_ids:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": (
                            f"memory_store {canonical_id} appears more than once in "
                            "resources[]; attach each store at most once per session"
                        ),
                    },
                )
            seen_memory_store_ids.add(canonical_id)
            store_row = await fetch_one(
                "SELECT id, name, description, archived_at "
                "FROM memory_stores WHERE id = $1 AND workspace_id = $2",
                store_uid,
                project_uid,
            )
            if store_row is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"Memory store {canonical_id} not found",
                    },
                )
            if store_row["archived_at"] is not None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"Memory store {canonical_id} is archived",
                    },
                )
            file_meta.append(None)
            memory_meta.append({
                "store_uid": store_uid,
                "name": store_row["name"],
                "description": store_row["description"],
            })
        elif isinstance(resource, VaultResource):
            # v0.3 PR6 — VaultResource entries piggy-back on the
            # ``unified_vault_ids`` validation pass above. Skip per-
            # resource work here so the parallel meta lists stay
            # aligned with body.resources by index.
            file_meta.append(None)
            memory_meta.append(None)
        elif isinstance(resource, (GitRepositoryResource, GithubRepositoryResource)):
            # v0.5 PR3 — actual clone happens after the env config is
            # loaded (so we have env_id for the cache key). Just keep
            # the parallel meta lists aligned for now; the real work
            # runs in the dedicated git_repository loop below.
            file_meta.append(None)
            memory_meta.append(None)
        else:
            raise HTTPException(
                status_code=501,
                detail={
                    "error": "not_implemented",
                    "message": (
                        f"resource type '{resource.type}' is not supported in v0.5; "
                        "see docs for the currently-dispatchable resource types"
                    ),
                },
            )

    # Per-session memory-store cap — mirrors Anthropic's max-8 default
    # (overridable via LINCHPIN_MEMORY_MAX_STORES_PER_SESSION). Enforce after
    # the validation loop so the error message reports the actual count.
    memory_resource_count = sum(1 for m in memory_meta if m is not None)
    cap = max_stores_per_session()
    if memory_resource_count > cap:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": (
                    f"session has {memory_resource_count} memory_store resources; "
                    f"cap is {cap} per session"
                ),
            },
        )

    # Determine network from environment config
    env_config = env_row["config"]
    if isinstance(env_config, str):
        env_config = json.loads(env_config)
    network = _network_for_environment(env_config)

    # Generate the session id up front so PR5's per-session outputs bind can
    # be set up before the container starts. The id is also used to scope the
    # deliverables watcher task.
    session_id = str(uuid.uuid4())

    # Build the read-only mount list for file resources (D1 — plain `:ro`
    # bind, no overlay2). Per-resource mount-time re-check catches files
    # deleted between upfront validation and now; failures get state='failed'
    # rows and a session.resource_mount_failed event after the session row
    # exists. FileStore is lazy-initialized only when there are resources to
    # mount so v0.1-shape session creates (no resources) don't trigger a
    # mkdir on LINCHPIN_FILES_ROOT.
    #
    # Memory stores skip this loop entirely — PR3 only registers them in
    # session_resources and materializes the host cache (below). PR4 turns
    # that cache into an actual sandbox bind + adds the per-store watcher.
    mounts: list[ResourceMount] = []
    resource_states: list[tuple[str, str | None]] = []  # parallel to body.resources
    seen_host_paths: dict[str, str] = {}  # host_path -> mount_path of first claimant
    has_file_resource = any(isinstance(r, FileResource) for r in body.resources)
    file_store = get_file_store() if has_file_resource else None
    for idx, resource in enumerate(body.resources):
        if not isinstance(resource, FileResource):
            # memory_store rows are recorded after the session row exists;
            # github_repository is rejected upstream. Anything else here is
            # a bug, not a runtime input.
            resource_states.append(("mounted", None))
            continue
        if file_store is None:
            raise RuntimeError("file_store unexpectedly None despite file resource present")
        meta = file_meta[idx]
        if meta is None:
            raise RuntimeError(
                f"file_meta[{idx}] is None but resource is FileResource — "
                "validation/mount-build loops are out of step"
            )
        host_path = file_store.absolute_path(meta["storage_path"])
        # Refuse two resources that resolve to the same backing host_path —
        # the FileStore is content-addressed so two distinct file_ids whose
        # bytes match share storage_path, and docker-py's ``volumes=`` shape
        # is host-path-keyed (it silently collapses dupes into the last one).
        # Returning 422 here means the API never reports state='mounted' for
        # a bind that didn't actually reach docker. Lifting this to "mount
        # same content at multiple paths" needs the docker-py ``mounts=`` API.
        if host_path in seen_host_paths:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "duplicate_mount_source",
                    "message": (
                        f"resources[{idx}] (file_id={resource.file_id}, "
                        f"mount_path={meta['mount_path']!r}) backs onto the "
                        "same content-addressed storage as an earlier resource "
                        f"already mounted at {seen_host_paths[host_path]!r}. "
                        "Mounting one file at multiple container paths is not "
                        "supported in v0.2.0; upload the file as a distinct "
                        "object or pick one mount path."
                    ),
                },
            )
        if not os.path.isfile(host_path):
            resource_states.append((
                "failed",
                (
                    f"file {resource.file_id} backing path is missing on disk "
                    f"(storage_path={meta['storage_path']!r})"
                ),
            ))
            continue
        seen_host_paths[host_path] = meta["mount_path"]
        mounts.append(ResourceMount(
            host_path=host_path,
            container_path=meta["mount_path"],
            mode="ro",
        ))
        resource_states.append(("mounted", None))

    # PR5 — writable bind for deliverables. The container sees
    # /mnt/session/outputs/ as writable; the watcher (asyncio task per
    # session, spawned below) mirrors host-side writes into the Files API.
    outputs_dir = ensure_session_outputs_dir(session_id)
    mounts.append(ResourceMount(
        host_path=str(outputs_dir),
        container_path="/mnt/session/outputs",
        mode="rw",
    ))

    # v0.3 PR3 + PR4 — materialize each attached memory_store into its
    # per-session host cache dir and add a bind mount so the container
    # sees it at /mnt/memory/<name>/. PR4 also spawns a watcher per
    # rw store after the session row exists (below). We materialize +
    # mount before container create so any hydration failure (disk full,
    # bad storage row) 500s cleanly with no orphan container or DB state
    # to roll back.
    for idx, resource in enumerate(body.resources):
        if not isinstance(resource, MemoryStoreResource):
            continue
        meta = memory_meta[idx]
        if meta is None:
            raise RuntimeError(
                f"memory_meta[{idx}] is None but resource is MemoryStoreResource — "
                "validation/materialize loops are out of step"
            )
        try:
            cache_dir = await materialize_memory_store(
                session_id=session_id,
                memory_store_id=meta["store_uid"],
                store_name=meta["name"],
            )
        except OSError as exc:
            logger.error(
                "materialize_memory_store failed (session=%s, store=%s): %s",
                session_id, meta["store_uid"], exc,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "memory_materialize_failed",
                    "message": (
                        f"failed to hydrate memory store {meta['store_uid']} "
                        f"into per-session cache: {exc}"
                    ),
                },
            ) from exc
        # Bind mount mode mirrors the resource's access field. Read-only
        # stores get a kernel-enforced ro bind, so the agent can't write
        # at all and PR4's watcher doesn't run for them. Read-write stores
        # get rw + a writeback watcher (spawned after session row insert).
        mounts.append(ResourceMount(
            host_path=cache_dir,
            container_path=f"/mnt/memory/{meta['name']}",
            mode="rw" if resource.access == "read_write" else "ro",
        ))

    # v0.4.0 — materialize the agent's skill bundles into per-session
    # cache dirs and bind-mount each at /mnt/skills/<name>/ read-only.
    # Skills are static-at-agent-time so they're resolved off the agent
    # row (pinned via agent_version), not via resources[]. Failures here
    # 500 — agents expect their skills to be present, unlike file
    # resources where missing-host-path is recoverable.
    # asyncpg Record.keys() is a one-shot iterator — materialize before `in`.
    agent_keys = set(agent_row.keys())
    skills_raw = agent_row["skills"] if "skills" in agent_keys else []
    if isinstance(skills_raw, str):
        skill_ids: list[str] = json.loads(skills_raw)
    else:
        skill_ids = list(skills_raw or [])
    attached_skills: list[dict[str, object]] = []
    if skill_ids:
        from app.skills import (
            absolute_storage_path,
            materialize_skill_bundle,
        )
        for sid in skill_ids:
            try:
                skill_uid = uuid.UUID(sid)
            except ValueError:
                logger.warning(
                    "agent %s has malformed skill id %r; skipping",
                    agent_uid, sid,
                )
                continue
            skill_row = await fetch_one(
                "SELECT id, name, description, bundle_sha256, archived_at "
                "FROM skills WHERE id = $1 AND workspace_id = $2",
                skill_uid,
                project_uid,
            )
            if skill_row is None or skill_row["archived_at"] is not None:
                # Skill was deleted after the agent was last patched —
                # log + skip so the session still boots. The metadata
                # block omits the missing skill so the agent won't try
                # to invoke it.
                logger.warning(
                    "skill %s missing or archived; session %s booting without it",
                    sid, session_id,
                )
                continue
            bundle_path = absolute_storage_path(skill_row["bundle_sha256"])
            try:
                cache_dir = await asyncio.to_thread(
                    materialize_skill_bundle,
                    bundle_path=bundle_path,
                    session_id=session_id,
                    skill_name=skill_row["name"],
                )
            except (FileNotFoundError, OSError) as exc:
                logger.error(
                    "materialize_skill_bundle failed (skill=%s, session=%s): %s",
                    sid, session_id, exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "skill_materialize_failed",
                        "message": (
                            f"failed to materialize skill {sid} bundle: {exc}"
                        ),
                    },
                ) from exc
            mounts.append(ResourceMount(
                host_path=cache_dir,
                container_path=f"/mnt/skills/{skill_row['name']}",
                mode="ro",
            ))
            attached_skills.append({
                "name": skill_row["name"],
                "description": skill_row["description"],
            })

    # v0.5 PR3 — clone each attached git_repository resource into the
    # per-environment cache and bind-mount it read-only at the
    # requested mount_path. Failures 500 cleanly: a partial clone is
    # not useful to the agent. Cache hits skip the network.
    for resource in body.resources:
        if not isinstance(
            resource, (GitRepositoryResource, GithubRepositoryResource)
        ):
            continue
        try:
            cache_dir = await clone_or_update(
                env_id=str(env_uid),
                url=resource.url,
                branch=resource.branch,
                shallow=resource.shallow,
                token=resource.authorization_token,
            )
        except GitCloneError as exc:
            logger.error(
                "git clone failed (session=%s, url=%s): %s",
                session_id, resource.url, exc,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "git_clone_failed",
                    "message": str(exc),
                },
            ) from exc
        mounts.append(ResourceMount(
            host_path=cache_dir,
            container_path=resource.mount_path,
            mode="ro",
        ))

    # Provision Docker container via sandbox with the mount list.
    # Atlas C3 — pick the runtime from the environment's isolation
    # setting ("docker" default; "microvm" when the deployment enables
    # LINCHPIN_ENABLE_MICROVM). This runs before the session row INSERT,
    # so a missing runtime surfaces as a 503 instead of a provisioning
    # failure on a row that was never created.
    try:
        sandbox = await resolve_sandbox_for_config(request.app.state, env_config)
    except SandboxError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "sandbox_runtime_unavailable",
                "message": str(exc),
            },
        ) from exc

    # v0.2.0 item #3 — derive a packaged image when the environment lists
    # any pre-install packages. Empty package set falls through to the
    # base image at no extra cost.
    base_image = os.getenv("LINCHPIN_SANDBOX_IMAGE", DEFAULT_BASE_IMAGE)
    packages_dict = env_config.get("packages") if isinstance(env_config, dict) else None
    env_packages = EnvironmentPackages.model_validate(packages_dict or {})

    # Atlas B3 — sandbox spec tier from the environment config.
    resources_dict = env_config.get("resources") if isinstance(env_config, dict) else None
    env_resources = SandboxResources.model_validate(resources_dict or {})

    # Atlas A2 — persist the session row BEFORE provisioning (status=
    # 'provisioning', container_id NULL). A crash mid-provisioning leaves
    # a traceable row recover_sessions understands, and image/container
    # failures land as error(provisioning_failed) + session.error instead
    # of vanishing behind a bare 500.
    agent_version = agent_row["version"]
    # Atlas C3 — pin the runtime the session was created with
    # ("docker"/"microvm") in session metadata so later lifecycle calls
    # (terminate/filesystem) resolve the same sandbox even if the
    # environment's isolation is edited afterwards.
    # Atlas M4 (FR-5.6) — snapshot the tool_search config into metadata so
    # the orchestrator loop can honor it without re-joining environments
    # every turn. Invalid shapes degrade to the legacy always-expose mode.
    ts_dict = env_config.get("tool_search") if isinstance(env_config, dict) else None
    ts_snapshot: dict | None = None
    if ts_dict is not None:
        try:
            ts_snapshot = ToolSearchConfig.model_validate(ts_dict).model_dump()
        except ValidationError:
            logger.warning("invalid tool_search config on environment; ignoring")
    metadata_payload: dict[str, Any] = {
        **body.metadata,
        "sandbox_runtime": runtime_for_config(env_config),
    }
    # Atlas M4 (FR-4.6) — pin the executor node (by name; names are UNIQUE
    # and stable, so the pin can be reversed to a row later) when the
    # environment targets one, so lifecycle calls resolve the same node.
    node_name = env_config.get("executor_node") if isinstance(env_config, dict) else None
    if node_name:
        metadata_payload["executor_node"] = node_name
    if ts_snapshot is not None:
        metadata_payload["tool_search"] = ts_snapshot
    metadata_json = json.dumps(metadata_payload)
    # v0.3 PR6 — persist the unified list (legacy vault_ids + any
    # VaultResource entries pulled from resources[]). Storage column
    # is unchanged; the fold-in only normalizes the input shape.
    vault_ids_json = json.dumps(unified_vault_ids)
    stats_json = json.dumps({"total_events": 0, "tool_calls": 0, "model_turns": 0})
    # v0.2.0 item #10 — prompt-cache counters seeded alongside the existing two.
    usage_json = json.dumps({
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    })

    # v0.2.0 breaking bundle — pin the session to the wire-format
    # version the caller used. NULL = v1 (no header sent). The events
    # endpoint reads this column to keep an in-flight stream's shape
    # consistent for the session's lifetime.
    from app.api_version import V1
    api_version = getattr(request.state, "api_version", V1)
    pinned_version: str | None = None if api_version == V1 else api_version

    # v0.6 PR1 — validate the outcome grader agent exists + isn't
    # archived (fail fast so the session row never lands pointing
    # at a deleted grader). Persisted as JSONB on the sessions row.
    outcome_json = None
    if body.outcome is not None:
        from app.models import OutcomeAgentGrader
        grader = body.outcome.grader
        if isinstance(grader, OutcomeAgentGrader):
            try:
                grader_uid = uuid.UUID(grader.agent_id)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"Invalid outcome.grader.agent_id: {grader.agent_id}",
                    },
                )
            grader_row = await fetch_one(
                "SELECT id, archived_at FROM agents WHERE id = $1 AND project_id = $2",
                grader_uid,
                project_uid,
            )
            if grader_row is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": (
                            f"outcome grader agent {grader.agent_id} not found"
                        ),
                    },
                )
            if grader_row["archived_at"] is not None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": (
                            f"outcome grader agent {grader.agent_id} is archived"
                        ),
                    },
                )
        outcome_json = body.outcome.model_dump_json()

    # Atlas B8 — envelope encryption (FR-10.3). When opted in via the
    # ``x-events-encrypted: true`` header, generate a per-session data key:
    # the plaintext key encrypts business-content event payloads, and a
    # wrapped copy is persisted so the key survives restarts. Atlas M6
    # BYOK — with ``x-kek-credential-id`` the wrapping key is the
    # customer's KEK (a byok_kek vault credential) instead of the
    # platform master key.
    data_key_plain: str | None = None
    data_key_wrapped: str | None = None
    if events_encrypted:
        data_key_plain = generate_data_key()
        if kek_credential_id:
            kek = await _resolve_kek_credential(kek_credential_id, body.vault_ids)
            data_key_wrapped = wrap_data_key(
                data_key_plain, kek=kek, kek_credential_id=kek_credential_id
            )
        else:
            data_key_wrapped = wrap_data_key(data_key_plain)

    row = await fetch_one(
        """
        INSERT INTO sessions
            (id, project_id, agent_id, agent_version, environment_id, status,
             container_id, title, metadata, ttl_seconds, vault_ids, stats, usage,
             linchpin_api_version, outcome, events_encrypted, data_key)
        VALUES ($1, $2, $3, $4, $5, 'provisioning',
                NULL, $6, $7::jsonb, $8, $9::jsonb, $10::jsonb, $11::jsonb, $12,
                $13::jsonb, $14, $15)
        RETURNING *
        """,
        uuid.UUID(session_id),
        project_uid,
        agent_uid,
        agent_version,
        env_uid,
        body.title,
        metadata_json,
        body.ttl_seconds,
        vault_ids_json,
        stats_json,
        usage_json,
        pinned_version,
        outcome_json,
        events_encrypted,
        data_key_wrapped,
    )

    # Cache the plaintext data key so this process can encrypt without a
    # DB roundtrip per event (B8). Only when encryption is on.
    if data_key_plain is not None:
        cache_session_data_key(session_id, data_key_plain)

    await record_audit(
        "session.created",
        "session",
        session_id,
        ctx=ctx,
        project_id=project_uid,
        detail={
            "title": body.title,
            "agent_id": str(body.agent_id),
            "events_encrypted": events_encrypted,
            "kek_credential_id": kek_credential_id,
        },
    )

    try:
        image = await sandbox.ensure_image(
            base_image=base_image, packages=env_packages
        )
    except SandboxError as exc:
        logger.error("sandbox.ensure_image failed: %s", exc)
        # Atlas A2 — the row already exists ('provisioning'): leave it as
        # error('provisioning_failed') + session.error instead of a bare 500.
        await _mark_provisioning_failed(
            session_id,
            f"sandbox.ensure_image failed: {exc}",
            "sandbox_image_build_failed",
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "sandbox_image_build_failed",
                "message": str(exc),
            },
        ) from exc

    # Atlas B4 — limited environments have no direct route out of the
    # internal bridge; all egress goes through a per-environment Squid
    # proxy whose allowlist is derived from the env config. Boot (or
    # reuse) it before the sandbox, then inject the proxy env vars into
    # the session container. none/unrestricted are unchanged.
    proxy_env: dict[str, str] | None = None
    if network == "linchpin-limited":
        try:
            proxy_env = await asyncio.to_thread(
                proxy_env_from_networking,
                str(env_uid),
                env_config.get("networking") or {},
            )
        except SandboxError as exc:
            logger.error("egress proxy boot failed: %s", exc)
            await _mark_provisioning_failed(
                session_id, f"egress proxy failed: {exc}", "egress_proxy_failed"
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "egress_proxy_failed",
                    "message": str(exc),
                },
            ) from exc

    try:
        container_id = await sandbox.create(
            image, network, mounts=mounts, resources=env_resources,
            env=proxy_env,
        )
    except SandboxError as exc:
        # Container creation is all-or-nothing in docker-py — if it fails,
        # there's no partial container to leave behind. The 'provisioning'
        # row persisted earlier is marked error instead of a bare 500.
        logger.error("sandbox.create failed: %s", exc)
        await _mark_provisioning_failed(
            session_id, f"sandbox.create failed: {exc}", "sandbox_create_failed"
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "sandbox_create_failed",
                "message": str(exc),
            },
        ) from exc

    # Everything below this point depends on the container we just created.
    # If any DB write fails (CHECK constraint, transient connection drop,
    # cancellation), we must destroy the container or it'll run forever with
    # no row pointing at it — recover_sessions only walks the sessions
    # table. Wrap the whole block and clean up on any failure.
    # (session_id was generated above so the PR5 outputs bind could use it.)
    try:
        # Atlas A2 — bind the freshly created container to the row first.
        await execute(
            "UPDATE sessions SET container_id = $2 WHERE id = $1",
            uuid.UUID(session_id),
            container_id,
        )

        # Persist session_resources rows. State is 'mounted' for resources whose
        # bind was added to the container (file) or whose host cache was
        # materialized (memory_store); 'failed' (with error) for file resources
        # whose host_path went missing between validation and mount.
        resources: list[SessionResource] = []
        failed_resources: list[tuple[uuid.UUID, str, str]] = []  # (resource_id, mount_path, error)
        for idx, resource in enumerate(body.resources):
            if isinstance(resource, FileResource):
                state, error = resource_states[idx]
                # Persist canonical UUID form (str(uuid.UUID(x))) so the DELETE 409
                # mount-conflict check (which uses str(file_uid) on the URL-supplied
                # id) matches regardless of whether the original POST sent the file
                # id with dashes, without dashes, in uppercase, or braced.
                resource_type = resource.type
                # Atlas B5 — persist the resolved mount path (explicit or the
                # /mnt/session/uploads/<basename> default assigned during
                # validation) so reads reflect what the container actually saw.
                resource_mount_path = file_meta[idx]["mount_path"]
                config_json = json.dumps({"file_id": str(uuid.UUID(resource.file_id))})
            elif isinstance(resource, MemoryStoreResource):
                meta = memory_meta[idx]
                if meta is None:
                    raise RuntimeError(
                        f"memory_meta[{idx}] is None at persist time — "
                        "validation/persist loops are out of step"
                    )
                state, error = "mounted", None
                resource_type = resource.type
                # /mnt/memory/<store-name>/ is the canonical container-side
                # path PR4 will bind to. We persist it now so the orchestrator
                # block + REST responses can reference the path without
                # round-tripping memory_stores.
                resource_mount_path = f"/mnt/memory/{meta['name']}"
                config_json = json.dumps({
                    "memory_store_id": str(meta["store_uid"]),
                    "access": resource.access,
                    "instructions": resource.instructions,
                })
            elif isinstance(resource, VaultResource):
                # v0.3 PR6 — VaultResource doesn't get a session_resources
                # row; the canonical surface is still sessions.vault_ids.
                # The fold-in is purely an input-shape convenience.
                continue
            elif isinstance(
                resource, (GitRepositoryResource, GithubRepositoryResource)
            ):
                # v0.5 PR3 — git repos clone into the env-scoped cache
                # above; here we just persist the mount + a redacted
                # config so the token isn't stored alongside the row.
                state, error = "mounted", None
                # Canonicalize to ``git_repository`` even when the caller
                # sent the legacy ``github_repository`` alias — downstream
                # rows + reads see one homogeneous type.
                resource_type = "git_repository"
                resource_mount_path = resource.mount_path
                config_json = json.dumps({
                    "url": resource.url,
                    "branch": resource.branch,
                    "shallow": resource.shallow,
                    # Deliberately do NOT persist authorization_token —
                    # it lives in the env's vault rotation, not in
                    # session_resources rows that surface via list/get.
                    "has_token": resource.authorization_token is not None,
                })
            else:
                raise RuntimeError(
                    f"unexpected resource type {type(resource).__name__} reached persist loop"
                )
            # Terminal states ('failed', 'unmounted') require unmounted_at per
            # the session_resources_terminal_has_unmounted_at CHECK in
            # migration 0004. Set unmounted_at=now() for pre-mount failures.
            unmounted_at = datetime.now(timezone.utc) if state == "failed" else None
            resource_row = await fetch_one(
                """
                INSERT INTO session_resources
                    (session_id, type, mount_path, config, state, error, unmounted_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
                RETURNING *
                """,
                uuid.UUID(session_id),
                resource_type,
                resource_mount_path,
                config_json,
                state,
                error,
                unmounted_at,
            )
            resources.append(_row_to_resource(resource_row))
            if state == "failed":
                failed_resources.append((resource_row["id"], resource_mount_path, error or ""))

        # Emit session.resource_mount_failed events for any pre-mount failures.
        # The session row is in place by this point so the events table's FK
        # constraint (session_id REFERENCES sessions) is satisfied. Spec line
        # 336: session still starts even when individual mounts fail.
        for resource_uid, mount_path, error in failed_resources:
            try:
                await append_event(
                    session_id,
                    "session.resource_mount_failed",
                    {
                        "resource_id": str(resource_uid),
                        "mount_path": mount_path,
                        "error": error,
                    },
                )
            except Exception:
                # Best-effort: event log is not load-bearing for session boot.
                logger.exception("failed to append session.resource_mount_failed event")

        # v0.3 PR6 — vault_ids deprecation signal. When the caller sent
        # the legacy ``vault_ids`` field (even an empty list won't trip
        # because legacy_vault_ids_supplied was set off a truthy check),
        # echo the deprecation back so SDKs can surface it to users and
        # emit one session.deprecation_used event so a downstream audit
        # query can see which clients are still on the old shape.
        if legacy_vault_ids_supplied:
            from app.api_version import (
                DEPRECATED_SESSION_VAULT_IDS,
                mark_deprecation,
            )
            mark_deprecation(response, DEPRECATED_SESSION_VAULT_IDS)
            try:
                await append_event(
                    session_id,
                    "session.deprecation_used",
                    {
                        "shape": DEPRECATED_SESSION_VAULT_IDS,
                        "migration": (
                            "Send vaults via resources[] entries with "
                            "type='vault' instead. vault_ids removed in v0.4."
                        ),
                    },
                )
            except Exception:
                logger.exception("failed to append session.deprecation_used event")

        # Atlas A2 — provisioning complete: flip to running so the send
        # gate admits the first user message while the orchestrator waits
        # on LISTEN, then refresh the row so the response reflects the
        # final status + bound container_id (INSERT RETURNING predates both).
        await transition(session_id, "running")
        row = await fetch_one(
            "SELECT * FROM sessions WHERE id = $1", uuid.UUID(session_id)
        )
    except BaseException as create_exc:
        # Destroy the orphaned container before re-raising so we don't leak
        # docker resources on any DB/cancellation failure post-create. Use
        # BaseException so client-disconnect (CancelledError) also cleans up.
        # Cascade-delete via FK ON DELETE CASCADE removes any partially-
        # written session_resources rows; we still issue an explicit DELETE
        # in case the sessions INSERT itself succeeded and the failure was
        # later (the FK cascade is what's load-bearing for resource rows).
        logger.error(
            "session create failed after container provisioning: %s; "
            "destroying orphan container %s",
            create_exc,
            container_id,
        )
        try:
            await sandbox.destroy(container_id)
        except Exception:
            logger.exception(
                "failed to destroy orphan container %s during session-create rollback",
                container_id,
            )
        try:
            await execute("DELETE FROM sessions WHERE id = $1", uuid.UUID(session_id))
        except Exception:
            logger.exception(
                "failed to delete partial session row %s during rollback", session_id,
            )
        raise

    # Start the orchestrator loop as a background async task
    task = asyncio.create_task(run_session(session_id, sandbox))

    def _task_done(t: asyncio.Task) -> None:
        exc = t.exception() if not t.cancelled() else None
        if exc:
            logger.error("Orchestrator task for session %s failed: %s", session_id, exc)

    task.add_done_callback(_task_done)
    request.app.state.orchestrator_tasks[session_id] = task

    # PR5 — spawn the deliverables watcher as a per-session asyncio task (D3).
    # Watches host-side /mnt/session/outputs/{sid} for new files written by
    # the agent and registers them as deliverables in the Files API. Cancelled
    # in terminate_session.
    watcher_task = asyncio.create_task(watch_session_deliverables(session_id))

    def _watcher_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error("Watcher task for session %s failed: %s", session_id, exc)

    watcher_task.add_done_callback(_watcher_done)
    watcher_tasks = getattr(request.app.state, "watcher_tasks", None)
    if watcher_tasks is None:
        watcher_tasks = {}
        request.app.state.watcher_tasks = watcher_tasks
    watcher_tasks[session_id] = watcher_task

    # v0.3 PR4 — spawn one memory writeback watcher per attached read_write
    # memory_store resource. Read-only stores get no watcher (the kernel
    # rejects writes at the bind mount). Each task is keyed by session id
    # so terminate_session can cancel them as a group.
    memory_watcher_tasks_by_session = getattr(request.app.state, "memory_watcher_tasks", None)
    if memory_watcher_tasks_by_session is None:
        memory_watcher_tasks_by_session = {}
        request.app.state.memory_watcher_tasks = memory_watcher_tasks_by_session
    spawned: list[asyncio.Task] = []
    for idx, resource in enumerate(body.resources):
        if not isinstance(resource, MemoryStoreResource):
            continue
        if resource.access != "read_write":
            continue
        meta = memory_meta[idx]
        if meta is None:
            continue
        mw_task = asyncio.create_task(
            watch_memory_store(
                session_id,
                meta["store_uid"],
                meta["name"],
            )
        )

        def _mw_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.error(
                    "Memory watcher task for session %s failed: %s", session_id, exc,
                )

        mw_task.add_done_callback(_mw_done)
        spawned.append(mw_task)
    if spawned:
        memory_watcher_tasks_by_session[session_id] = spawned

    result = _row_to_session(row, resources=resources)
    if idempotency_key:
        await idempotency_record(
            idempotency_key, idem_endpoint, idem_hash, 201,
            result.model_dump(mode="json"),
        )
    return result


@router.get("", response_model=PaginatedListResponse)
async def list_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    agent_id: str | None = Query(default=None),
    deployment_id: str | None = Query(default=None),  # Atlas B7 run history
    ctx: AuthContext = Depends(require_auth),
) -> PaginatedListResponse:
    """Return a paginated list of sessions, optionally filtered by agent_id
    and/or deployment_id (B7 — a deployment's run history is simply its
    sessions)."""
    project_uid = uuid.UUID(ctx.project_id)

    # B7 — resolve the deployment filter (404 on unknown/foreign ids so
    # run-history polling surfaces typos instead of an empty-looking 200).
    deployment_uid: uuid.UUID | None = None
    if deployment_id is not None:
        try:
            deployment_uid = uuid.UUID(deployment_id)
        except ValueError:
            return PaginatedListResponse(data=[], has_more=False, next_cursor=None)
        dep_row = await fetch_one(
            "SELECT id FROM deployments WHERE id = $1 AND project_id = $2",
            deployment_uid,
            project_uid,
        )
        if dep_row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "not_found",
                    "message": f"Deployment {deployment_id} not found",
                },
            )

    if agent_id is not None:
        try:
            agent_uid = uuid.UUID(agent_id)
        except ValueError:
            return PaginatedListResponse(data=[], has_more=False, next_cursor=None)

        if deployment_uid is not None:
            rows = await fetch_all(
                """SELECT * FROM sessions
                   WHERE agent_id = $1 AND deployment_id = $3 AND project_id = $2
                   ORDER BY created_at DESC
                   LIMIT $4 OFFSET $5""",
                agent_uid,
                project_uid,
                deployment_uid,
                limit + 1,
                offset,
            )
        else:
            rows = await fetch_all(
                """SELECT * FROM sessions
                   WHERE agent_id = $1 AND project_id = $2
                   ORDER BY created_at DESC
                   LIMIT $3 OFFSET $4""",
                agent_uid,
                project_uid,
                limit + 1,
                offset,
            )
    elif deployment_uid is not None:
        rows = await fetch_all(
            """SELECT * FROM sessions
               WHERE deployment_id = $1 AND project_id = $2
               ORDER BY created_at DESC
               LIMIT $3 OFFSET $4""",
            deployment_uid,
            project_uid,
            limit + 1,
            offset,
        )
    else:
        rows = await fetch_all(
            """SELECT * FROM sessions
               WHERE project_id = $1
               ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
            project_uid,
            limit + 1,
            offset,
        )

    has_more = len(rows) > limit
    items = rows[:limit]

    # Batch-load resources for all returned sessions in one query (avoids N+1).
    session_ids = [r["id"] for r in items]
    resources_by_session: dict[uuid.UUID, list[SessionResource]] = {sid: [] for sid in session_ids}
    if session_ids:
        resource_rows = await fetch_all(
            """SELECT * FROM session_resources
               WHERE session_id = ANY($1)
               ORDER BY created_at ASC""",
            session_ids,
        )
        for r in resource_rows:
            resources_by_session.setdefault(r["session_id"], []).append(_row_to_resource(r))

    sessions = [_row_to_session(r, resources=resources_by_session[r["id"]]) for r in items]

    return PaginatedListResponse(data=sessions, has_more=has_more, next_cursor=None)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> SessionResponse:
    """Retrieve a session by id with stats and usage."""
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    row = await fetch_one(
        "SELECT * FROM sessions WHERE id = $1 AND project_id = $2",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )
    resources = await _load_session_resources(uid)
    return _row_to_session(row, resources=resources)


@router.post("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    body: dict[str, Any],
    ctx: AuthContext = Depends(require_auth),
) -> SessionResponse:
    """Update session title and/or metadata."""
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    project_uid = uuid.UUID(ctx.project_id)
    row = await fetch_one(
        "SELECT * FROM sessions WHERE id = $1 AND project_id = $2", uid, project_uid,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    # Build SET clauses for provided fields
    sets: list[str] = ["updated_at = now()"]
    args: list[Any] = []
    idx = 1

    if "title" in body:
        sets.append(f"title = ${idx}")
        args.append(body["title"])
        idx += 1

    if "metadata" in body:
        sets.append(f"metadata = ${idx}::jsonb")
        args.append(json.dumps(body["metadata"]))
        idx += 1

    args.append(uid)
    args.append(project_uid)
    set_clause = ", ".join(sets)

    updated = await fetch_one(
        f"UPDATE sessions SET {set_clause} "
        f"WHERE id = ${idx} AND project_id = ${idx + 1} RETURNING *",
        *args,
    )

    resources = await _load_session_resources(uid)
    return _row_to_session(updated, resources=resources)


@router.delete("/{session_id}", response_model=SessionResponse)
async def terminate_session(
    session_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
) -> SessionResponse:
    """Terminate a session: set status to terminated, destroy container."""
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    project_uid = uuid.UUID(ctx.project_id)
    row = await fetch_one(
        "SELECT * FROM sessions WHERE id = $1 AND project_id = $2", uid, project_uid,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    # Update status to terminated
    updated = await fetch_one(
        """UPDATE sessions
           SET status = 'terminated', updated_at = now()
           WHERE id = $1 AND project_id = $2
           RETURNING *""",
        uid,
        project_uid,
    )

    # v0.6 PR2 — if this session is a thread, emit
    # ``session.thread_terminated`` on the parent so a coordinator
    # watching its own event stream learns the worker finished.
    keys = set(updated.keys()) if updated is not None else set()
    parent_session_id = (
        updated["parent_session_id"]
        if updated is not None and "parent_session_id" in keys
        else None
    )
    if parent_session_id is not None:
        try:
            await append_event(
                str(parent_session_id),
                "session.thread_terminated",
                {"thread_id": session_id},
            )
        except Exception:
            logger.exception(
                "failed to append session.thread_terminated event "
                "to parent %s", parent_session_id,
            )

    # Transition all live session_resources rows to 'unmounted'. Destroying
    # the container removes every bind, so the rows are no longer mounted;
    # this UPDATE makes the DELETE /v1/files 409 check (which filters on
    # state IN ('mounted','unmounting')) reflect reality. Without it, files
    # would become permanently undeletable after first session use even
    # though the underlying bind no longer exists.
    await execute(
        """UPDATE session_resources
           SET state = 'unmounted', unmounted_at = now()
           WHERE session_id = $1
             AND state IN ('mounted', 'unmounting')""",
        uid,
    )

    # Cancel the orchestrator task if it's running
    orchestrator_tasks = getattr(request.app.state, "orchestrator_tasks", {})
    task = orchestrator_tasks.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()

    # PR5 — cancel the deliverables watcher task. Boot scan on next start
    # (D4) picks up anything written between now and a future restart, so
    # the cancel is safe even if there are in-flight deliverables. Await
    # the cancellation so the rmtree below doesn't race a still-running
    # ingest (POSIX unlink-while-open is safe but we want the watcher's
    # logs to finish before we wipe its directory).
    watcher_tasks = getattr(request.app.state, "watcher_tasks", {})
    watcher_task = watcher_tasks.pop(session_id, None)
    if watcher_task is not None and not watcher_task.done():
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):
            pass

    # v0.3 PR4 — cancel every memory writeback watcher attached to this
    # session. Boot scan reconciles any drift on the next session resume
    # (there isn't one for terminated sessions, but the symmetry with the
    # deliverables watcher's restart story keeps the model coherent).
    memory_watcher_tasks = getattr(request.app.state, "memory_watcher_tasks", {})
    mw_tasks = memory_watcher_tasks.pop(session_id, [])
    for mw_task in mw_tasks:
        if not mw_task.done():
            mw_task.cancel()
    for mw_task in mw_tasks:
        try:
            await mw_task
        except (asyncio.CancelledError, Exception):
            pass

    # PR5 — terminal cleanup of the session's writable bind directory.
    # During the session this directory is the agent's workspace (the
    # source files are deliberately retained even after the watcher mirrors
    # them into the FileStore). On terminate the workspace is dead state
    # and would otherwise leak up to LINCHPIN_DELIVERABLES_PER_SESSION_CAP_BYTES
    # per session permanently.
    await asyncio.to_thread(
        shutil.rmtree, str(session_outputs_dir(session_id)), True,
    )

    # v0.3 PR4 — wipe the per-session memory cache dir. The canonical
    # state is in DB + FileStore (cache is regenerable). Path mirrors
    # the session_memory_cache_dir() layout: parent of any single store
    # dir is the per-session memory root.
    memory_cache_root = os.path.dirname(
        session_memory_cache_dir(session_id, "_placeholder")
    )
    await asyncio.to_thread(shutil.rmtree, memory_cache_root, True)

    # v0.4.0 — wipe the per-session skills cache dir too. Skill bundles
    # are regenerable from the canonical FileStore copies, so the
    # extracted per-session tree is purely working state.
    from app.skills import session_skills_root
    await asyncio.to_thread(
        shutil.rmtree, session_skills_root(session_id), True,
    )

    # Drop the per-session events.append lock so the dict doesn't grow
    # unboundedly across the api process's lifetime.
    release_session_event_lock(session_id)

    # Atlas B8 — drop the cached plaintext data key; the wrapped copy on
    # the row is dead state once the session is terminated.
    drop_session_data_key(session_id)

    # Destroy the container
    container_id = row["container_id"]
    if container_id:
        # Atlas C3 / M4 — route to the runtime pinned on the session
        # (docker vs microvm vs remote executor node), not whatever the
        # app is currently wired to.
        sandbox = await resolve_session_sandbox(request.app.state, row)
        await sandbox.destroy(container_id)

    # Atlas B12 — data purge on terminate (FR-1.4/FR-10.5): events and
    # session-scoped files are session-private state that must not outlive
    # the session. File bytes are content-addressed and may be shared, so
    # they are only dropped when no other live row references the same
    # storage_path.
    events_status = await execute(
        "DELETE FROM events WHERE session_id = $1", uid,
    )
    try:
        # str() — asyncpg returns "DELETE <n>"; stray mock objects fall into
        # the ValueError branch instead of blowing up the endpoint.
        events_deleted = int(str(events_status).split()[-1])
    except (ValueError, IndexError):
        events_deleted = -1

    scoped_files = await fetch_all(
        """SELECT id, storage_path FROM files
           WHERE scope_type = 'session' AND scope_id = $1""",
        uid,
    )
    files_deleted = len(scoped_files)
    if scoped_files:
        await execute(
            "DELETE FROM files WHERE scope_type = 'session' AND scope_id = $1",
            uid,
        )
        store = get_file_store()
        for f in scoped_files:
            still_referenced = await fetch_one(
                "SELECT 1 FROM files WHERE storage_path = $1 LIMIT 1",
                f["storage_path"],
            )
            if still_referenced is None:
                await store.delete(f["storage_path"])

    await record_audit(
        "session.deleted",
        "session",
        session_id,
        ctx=ctx,
        project_id=ctx.project_id,
        detail={
            "container_destroyed": bool(row["container_id"]),
            "events_deleted": events_deleted,
            "files_deleted": files_deleted,
        },
    )

    resources = await _load_session_resources(uid)
    return _row_to_session(updated, resources=resources)


# ---- v0.6 PR1 — outcome evaluations ----


class _PostOutcomeEvaluation(BaseModel):
    """Body shape for ``POST /v1/sessions/{id}/outcome_evaluations``.

    Either the grader agent itself or an external caller can submit a
    result. The route is the single canonical write path for outcome
    evaluations — auto-termination + event emission both fan out from
    here so a deterministic grader (future) and an agent grader (v0.6)
    converge on identical session-side behavior.
    """

    score: float = Field(ge=0.0, le=1.0)
    rationale: str | None = None
    criteria: list[dict[str, object]] = Field(default_factory=list)
    grader_id: str | None = None


@router.post(
    "/{session_id}/outcome_evaluations",
    status_code=201,
    response_model=OutcomeEvaluation,
)
async def post_outcome_evaluation(
    session_id: str,
    body: _PostOutcomeEvaluation,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
) -> OutcomeEvaluation:
    """Record a grader evaluation.

    Auto-terminates the session when ``score >= outcome.success_threshold``
    and the outcome declared ``auto_terminate: true`` (the default).
    Emits ``session.outcome_evaluation_ended`` regardless of threshold.
    """
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )
    sess_row = await fetch_one(
        "SELECT * FROM sessions WHERE id = $1 AND project_id = $2",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if sess_row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    grader_uid = None
    if body.grader_id:
        try:
            grader_uid = uuid.UUID(body.grader_id)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": f"Invalid grader_id: {body.grader_id}",
                },
            )

    eval_id = uuid.uuid4()
    row = await fetch_one(
        """
        INSERT INTO outcome_evaluations
            (id, session_id, grader_id, score, rationale, criteria)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING *
        """,
        eval_id,
        uid,
        grader_uid,
        body.score,
        body.rationale,
        json.dumps(body.criteria),
    )

    # Emit ``session.outcome_evaluation_ended`` so external listeners
    # (webhooks, SSE) learn the result without polling.
    try:
        await append_event(
            session_id,
            "session.outcome_evaluation_ended",
            {
                "evaluation_id": str(eval_id),
                "score": body.score,
                "rationale": body.rationale,
                "grader_id": body.grader_id,
            },
        )
    except Exception:
        logger.exception("failed to append session.outcome_evaluation_ended event")

    # Auto-terminate when the score crosses the success threshold and
    # the outcome opted into auto-termination. We pull the threshold
    # off the row (not the request) so a malicious grader can't lower
    # the bar by inlining their own.
    outcome_raw = sess_row.get("outcome") if hasattr(sess_row, "get") else sess_row["outcome"]
    if isinstance(outcome_raw, str):
        outcome_raw = json.loads(outcome_raw) if outcome_raw else None
    if outcome_raw:
        threshold = float(outcome_raw.get("success_threshold", 0.8))
        if outcome_raw.get("auto_terminate", True) and body.score >= threshold:
            if sess_row["status"] != "terminated":
                await execute(
                    "UPDATE sessions SET status = 'terminated', updated_at = NOW() "
                    "WHERE id = $1 AND status != 'terminated'",
                    uid,
                )
                try:
                    await append_event(
                        session_id,
                        "session.status_terminated",
                        {
                            "reason": "outcome_success",
                            "score": body.score,
                            "threshold": threshold,
                        },
                    )
                except Exception:
                    logger.exception(
                        "failed to append session.status_terminated event after outcome success",
                    )

    return OutcomeEvaluation(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        grader_id=str(row["grader_id"]) if row["grader_id"] is not None else None,
        score=float(row["score"]),
        rationale=row["rationale"],
        criteria=(
            json.loads(row["criteria"])
            if isinstance(row["criteria"], str)
            else (row["criteria"] or [])
        ),
        created_at=row["created_at"],
    )


@router.get(
    "/{session_id}/outcome_evaluations",
    response_model=OutcomeEvaluationsResponse,
)
async def list_outcome_evaluations(
    session_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> OutcomeEvaluationsResponse:
    """Append-only history of grader evaluations for a session, newest
    first. Empty list when no outcome was declared or no grader has
    run yet."""
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )
    sess_row = await fetch_one(
        "SELECT id FROM sessions WHERE id = $1 AND project_id = $2",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if sess_row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )
    rows = await fetch_all(
        "SELECT * FROM outcome_evaluations WHERE session_id = $1 "
        "ORDER BY created_at DESC",
        uid,
    )
    data = [
        OutcomeEvaluation(
            id=str(r["id"]),
            session_id=str(r["session_id"]),
            grader_id=str(r["grader_id"]) if r["grader_id"] is not None else None,
            score=float(r["score"]),
            rationale=r["rationale"],
            criteria=(
                json.loads(r["criteria"])
                if isinstance(r["criteria"], str)
                else (r["criteria"] or [])
            ),
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return OutcomeEvaluationsResponse(data=data)


# ---- v0.6 PR2 — multi-agent threads ----


class _SpawnThreadRequest(BaseModel):
    """Body shape for ``POST /v1/sessions/{parent_id}/threads``.

    Most fields shadow ``CreateSessionRequest`` so a coordinator can
    spawn a worker with any agent + environment + resources combo —
    the spawn is just a normal session-create with the parent link
    overlaid.
    """

    agent_id: str
    environment_id: str
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int | None = None
    vault_ids: list[str] = Field(default_factory=list)
    resources: list[Any] = Field(default_factory=list)
    input_message: str | dict | list | None = None
    outcome: OutcomeDefinition | None = None


class ThreadListResponse(BaseModel):
    data: list[SessionResponse]


@router.post(
    "/{parent_id}/threads",
    status_code=201,
    response_model=SessionResponse,
)
async def spawn_thread(
    parent_id: str,
    body: _SpawnThreadRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
) -> SessionResponse:
    """Spawn a worker thread off ``parent_id``.

    The new session is created via the same machinery as a normal
    ``POST /v1/sessions`` (validation, container provisioning,
    resources, …) but with two differences:

    1. ``parent_session_id`` is set to ``parent_id``.
    2. The nesting cap (``LINCHPIN_MAX_THREAD_DEPTH``, default 3) is
       enforced — a coordinator-of-coordinator-of-coordinator chain
       rejects rather than billing the user for runaway loops.

    On success, ``session.thread_created`` lands on the parent's
    event log so a coordinator watching its own stream learns about
    its worker without polling.
    """
    from app.threads import ThreadDepthExceeded, ensure_can_spawn_thread

    try:
        parent_uid = uuid.UUID(parent_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {parent_id} not found"},
        )
    parent_row = await fetch_one(
        "SELECT id, archived_at FROM sessions WHERE id = $1 AND project_id = $2",
        parent_uid,
        uuid.UUID(ctx.project_id),
    )
    if parent_row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {parent_id} not found"},
        )
    if parent_row["archived_at"] is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "conflict",
                "message": f"parent session {parent_id} is archived",
            },
        )

    try:
        await ensure_can_spawn_thread(parent_uid)
    except ThreadDepthExceeded as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "thread_depth_exceeded", "message": str(exc)},
        )

    # Forward to the main ``create_session`` flow by building a
    # CreateSessionRequest. The thread is otherwise a normal session.
    create_payload = {
        "agent_id": body.agent_id,
        "environment_id": body.environment_id,
        "title": body.title,
        "metadata": body.metadata,
        "ttl_seconds": body.ttl_seconds,
        "vault_ids": body.vault_ids,
        "resources": body.resources,
    }
    if body.outcome is not None:
        create_payload["outcome"] = body.outcome.model_dump()
    req = CreateSessionRequest.model_validate(create_payload)
    # Reuse the response object the route would have built so the
    # caller gets the same shape. Two side effects need to happen
    # after the normal create lands: stamp parent_session_id, and
    # emit the parent's session.thread_created event.
    response = Response()
    # A8 — pass idempotency_key explicitly: an internal direct call does not
    # go through FastAPI DI, so the Header() FieldInfo default would leak in
    # as a truthy "key" and trigger a DB replay lookup. Same for B8's
    # events_encrypted (the FieldInfo object is truthy) — threads spawn
    # plaintext unless a future spec opts them in explicitly. M6 BYOK's
    # kek_credential_id has the same leak shape — pass None explicitly.
    new_session = await create_session(
        req, request, response,
        idempotency_key=None, events_encrypted=False,
        kek_credential_id=None, ctx=ctx,
    )  # type: ignore[arg-type]

    # Stamp parent_session_id directly — done after create so the
    # main path stays simple and the column is the only place we
    # diverge from a top-level session.
    await execute(
        "UPDATE sessions SET parent_session_id = $1 WHERE id = $2",
        parent_uid,
        uuid.UUID(new_session.id),
    )
    # Seed the worker with an initial user message if one was given.
    if body.input_message is not None:
        try:
            await append_event(
                new_session.id,
                "user.message",
                {"content": body.input_message},
            )
        except Exception:
            logger.exception(
                "failed to seed thread %s with input_message", new_session.id,
            )

    # Notify the parent stream so a coordinator watching its event
    # log learns the worker is up.
    try:
        await append_event(
            parent_id,
            "session.thread_created",
            {
                "thread_id": new_session.id,
                "agent_id": body.agent_id,
            },
        )
    except Exception:
        logger.exception("failed to append session.thread_created event")

    new_session = new_session.model_copy(update={"parent_session_id": parent_id})
    return new_session


@router.get(
    "/{parent_id}/threads",
    response_model=ThreadListResponse,
)
async def list_threads(
    parent_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> ThreadListResponse:
    """Return every thread (including terminated) for a parent session."""
    try:
        parent_uid = uuid.UUID(parent_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {parent_id} not found"},
        )
    parent_row = await fetch_one(
        "SELECT id FROM sessions WHERE id = $1 AND project_id = $2",
        parent_uid,
        uuid.UUID(ctx.project_id),
    )
    if parent_row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {parent_id} not found"},
        )
    rows = await fetch_all(
        "SELECT * FROM sessions WHERE parent_session_id = $1 "
        "ORDER BY created_at ASC",
        parent_uid,
    )
    return ThreadListResponse(
        data=[_row_to_session(r, resources=[]) for r in rows],
    )


@router.post("/{session_id}/archive", response_model=SessionResponse)
async def archive_session(
    session_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> SessionResponse:
    """Archive a session: set archived_at.

    Atlas A6 — idempotent: archiving an already-archived session returns
    its current state (200) instead of 409, matching agents/environments.
    """
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    project_uid = uuid.UUID(ctx.project_id)
    row = await fetch_one(
        "SELECT * FROM sessions WHERE id = $1 AND project_id = $2", uid, project_uid,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    if row["archived_at"] is not None:
        resources = await _load_session_resources(uid)
        return _row_to_session(row, resources=resources)

    updated = await fetch_one(
        """UPDATE sessions
           SET archived_at = now(), updated_at = now()
           WHERE id = $1 AND project_id = $2
           RETURNING *""",
        uid,
        project_uid,
    )
    await record_audit("session.archived", "session", session_id, ctx=ctx)

    resources = await _load_session_resources(uid)
    return _row_to_session(updated, resources=resources)


# ---- POST /v1/sessions/{id}/events ----


@router.get("/{session_id}/streaming")
async def get_session_streaming(
    session_id: str,
    ctx: AuthContext = Depends(require_auth),
):
    """Get the in-flight streaming state for a session.

    Returns the accumulated text so far if the model is currently generating,
    or null if no active stream.
    """
    # C1 — tenant boundary: same 404 contract as the other session reads.
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )
    row = await fetch_one(
        "SELECT id FROM sessions WHERE id = $1 AND project_id = $2",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )
    state = await get_stream(session_id)
    return {"streaming": state}


@router.get("/{session_id}/events", response_model=PaginatedEventsResponse)
async def get_session_events(
    session_id: str,
    after_cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    types: list[str] | None = Query(default=None, alias="types[]"),
    ctx: AuthContext = Depends(require_auth),
) -> PaginatedEventsResponse:
    """Get events for a session with cursor-based pagination.

    v0.2.0 item #12 — optional ``?types[]=X&types[]=Y`` filter restricts the
    response to events whose ``type`` is in the list. Unknown type strings
    return 422 with the offender, rather than a silent empty result set.
    """
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    row = await fetch_one(
        "SELECT * FROM sessions WHERE id = $1 AND project_id = $2",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    # `?types[]=` (no value) is parsed by Starlette as [""], not None. Treat
    # blank/whitespace entries as "no filter" so an empty query string behaves
    # the same as omitting the param — the documented contract.
    if types is not None:
        types = [t for t in types if t and t.strip()]
        if not types:
            types = None

    # Validate every requested type against EVENT_TYPES so we return a
    # clean 422 with the offender, rather than a silent empty result set.
    if types:
        from app.models import EVENT_TYPES
        invalid = [t for t in types if t not in EVENT_TYPES]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_event_type",
                    "message": (
                        f"unknown event type(s): {invalid!r}. "
                        f"Valid types: {sorted(EVENT_TYPES)}"
                    ),
                    "invalid": invalid,
                },
            )

    result = await get_events(session_id, after_cursor=after_cursor, limit=limit, types=types)
    return result


@router.post("/{session_id}/events", status_code=201, response_model=list[EventResponse])
async def post_events(
    session_id: str,
    body: PostEventsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: AuthContext = Depends(require_auth),
) -> list[EventResponse]:
    """Post events to a session.

    Validates event types (422 via Pydantic), checks session exists (404),
    checks session is not archived (409), enforces the A4 state-gating
    matrix (409 with the blocked types when the current state won't accept
    them), then appends the whole batch and triggers LISTEN/NOTIFY on the
    session channel.

    Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 10.4
    """
    # Atlas A8 — replay a completed response for this Idempotency-Key
    # before any write. Key reuse with a different body is 409.
    idem_endpoint = f"POST /v1/sessions/{session_id}/events"
    idem_hash = compute_request_hash(
        "POST", f"/v1/sessions/{session_id}/events", body.model_dump(mode="json")
    )
    if idempotency_key:
        try:
            replayed = await idempotency_replay(idempotency_key, idem_endpoint, idem_hash)
        except IdempotencyHashMismatch:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "conflict",
                    "message": (
                        f"Idempotency-Key {idempotency_key!r} was already used "
                        "with a different request body"
                    ),
                },
            )
        if replayed is not None:
            replay_status, replay_body = replayed
            return JSONResponse(status_code=replay_status, content=replay_body)

    # Validate session_id is a valid UUID
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    # Check session exists (scoped to the caller's project)
    row = await fetch_one(
        "SELECT * FROM sessions WHERE id = $1 AND project_id = $2",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    # Check session is not archived
    if row["archived_at"] is not None:
        raise HTTPException(
            status_code=409,
            detail={"error": "conflict", "message": f"Session {session_id} is archived"},
        )

    # Atlas A4 — batch-atomic state gating. The whole batch is checked
    # against the session's current state before anything is written, so
    # a rejected batch leaves the event log completely untouched.
    status = row["status"]
    stop_reason = row.get("stop_reason")
    if status == "provisioning":
        # Not running yet — only cancellation makes sense.
        allowed = frozenset({"user.interrupt"})
    elif status == "running":
        allowed = frozenset({"user.message", "user.steer", "user.interrupt"})
    elif status == "idle" and stop_reason == "requires_action":
        # Waiting on a tool decision — only tool responses (or cancel).
        allowed = frozenset({
            "user.custom_tool_result",
            "user.tool_confirmation",
            "user.interrupt",
        })
    elif status == "idle":
        allowed = frozenset({"user.message", "user.interrupt"})
    elif status in ("error", "terminated", "failed"):
        allowed = frozenset({"user.interrupt"})
    else:
        # rescheduling and any future transitional state behave like running.
        allowed = frozenset({"user.message", "user.steer", "user.interrupt"})
    blocked = sorted({ev.type for ev in body.events if ev.type not in allowed})
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "conflict",
                "message": (
                    f"Session {session_id} in state {status}"
                    + (f" ({stop_reason})" if stop_reason else "")
                    + f" does not accept events: {', '.join(blocked)}"
                ),
                "blocked": blocked,
                "status": status,
                "stop_reason": stop_reason,
            },
        )

    # v0.6 PR2 — if this session is a thread, mirror each event up to
    # the parent (and on up the chain) so a coordinator watching its
    # own stream sees one interleaved view. The original event still
    # lands on the thread's log too, so per-thread reads aren't lost.
    from app.threads import thread_event_payload
    parent_id_for_mirror = row.get("parent_session_id") if hasattr(row, "get") else row["parent_session_id"]
    if parent_id_for_mirror is not None:
        parent_chain = [str(parent_id_for_mirror)]
        # Walk up to the root so a deeply-nested thread shows in every
        # ancestor's event log, not only its immediate parent.
        current = parent_id_for_mirror
        while True:
            anc = await fetch_one(
                "SELECT parent_session_id FROM sessions WHERE id = $1",
                current,
            )
            if anc is None or anc["parent_session_id"] is None:
                break
            current = anc["parent_session_id"]
            parent_chain.append(str(current))
    else:
        parent_chain = []

    # Append each event
    created_events: list[EventResponse] = []
    for ev in body.events:
        event = await append_event(session_id, ev.type, ev.payload)
        # Atlas B2 — set the in-process interrupt flag so the streaming
        # loop and tool-dispatch race see the interrupt without hitting
        # the DB per chunk. Cleared by the orchestrator when consumed.
        if ev.type == "user.interrupt":
            interrupts.request_interrupt(session_id)
        created_events.append(
            EventResponse(
                session_id=event.session_id,
                cursor=event.cursor,
                seq=event.seq,
                type=event.type,
                payload=event.payload,
                processed_at=event.processed_at,
                event_id=event.event_id,
            )
        )
        # Mirror to every ancestor so each level of the coordinator
        # tree sees the thread's activity. The thread_id payload tag
        # is added so listeners can filter / attribute events.
        for ancestor in parent_chain:
            try:
                await append_event(
                    ancestor,
                    ev.type,
                    thread_event_payload(session_id, ev.payload),
                )
                # Atlas B2 — mirrored interrupts also flag the ancestor's
                # orchestrator (matches what its DB check would see).
                if ev.type == "user.interrupt":
                    interrupts.request_interrupt(ancestor)
            except Exception:
                logger.exception(
                    "failed to mirror %s event from thread %s to ancestor %s",
                    ev.type, session_id, ancestor,
                )

    # Trigger LISTEN/NOTIFY — replace hyphens with underscores for PG channel name validity
    channel = f"session_{session_id.replace('-', '_')}"
    await notify(channel, "new_events")
    # And NOTIFY each ancestor channel so subscribers tailing the
    # coordinator's stream wake up on a thread write.
    for ancestor in parent_chain:
        await notify(f"session_{ancestor.replace('-', '_')}", "new_events")

    if idempotency_key:
        await idempotency_record(
            idempotency_key,
            idem_endpoint,
            idem_hash,
            201,
            [e.model_dump(mode="json") for e in created_events],
        )
    return created_events


# ---- GET /v1/sessions/{id}/stream (+ /events/stream alias) ----


def _sse_frame(event) -> dict:
    """Build one Atlas A3 SSE frame: ``id: <seq>``, ``event: <type>``,
    ``data: <json>``. The resume cursor stays per-session seq (monotonic);
    ``event_id`` adds a globally unique identity inside data."""
    data = json.dumps({
        "session_id": event.session_id,
        "type": event.type,
        "cursor": event.cursor,
        "seq": event.seq,
        "event_id": event.event_id,
        "payload": event.payload,
        "processed_at": str(event.processed_at) if event.processed_at else None,
    })
    return {"id": str(event.seq), "event": event.type, "data": data}


@router.get("/{session_id}/stream")
@router.get("/{session_id}/events/stream")
async def stream_session(
    session_id: str,
    cursor: str | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ctx: AuthContext = Depends(require_auth),
):
    """SSE stream endpoint: replay existing events then live-stream new ones.

    - Validates session exists (404 if not).
    - Resume point: ``Last-Event-ID`` header (Atlas A3) takes priority over
      ``?cursor=``; its value is the ``id:`` of the last frame seen — the
      per-session ``seq``. A raw cursor value is also tolerated.
    - If a resume point is provided, validates it (422 if invalid).
    - Phase 1: Replay all events after the resume point (or from beginning).
    - Phase 2: Subscribe to PG LISTEN/NOTIFY and yield new events as they arrive.
    - On client disconnect, cleans up the LISTEN subscription.

    Validates: Requirements 7.1, 7.2, 7.4
    """
    # Validate session_id is a valid UUID
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    # Check session exists (scoped to the caller's project)
    row = await fetch_one(
        "SELECT * FROM sessions WHERE id = $1 AND project_id = $2",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )

    # Atlas A3 — Last-Event-ID wins over ?cursor=. Frames carry ``id:`` as
    # the per-session seq, so a numeric header maps straight back to a
    # cursor; a raw cursor value is accepted for older clients.
    effective_cursor = cursor
    if last_event_id is not None:
        lid = last_event_id.strip()
        if lid.isdigit():
            effective_cursor = encode_cursor(int(lid))
        else:
            try:
                decode_cursor(lid)
                effective_cursor = lid
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": f"Invalid Last-Event-ID: {last_event_id}",
                    },
                )

    # Validate cursor if provided
    if effective_cursor is not None:
        try:
            decode_cursor(effective_cursor)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={"error": "validation_error", "message": f"Invalid cursor: {effective_cursor}"},
            )

    async def event_generator():
        last_cursor = effective_cursor

        # Phase 1: Replay existing events
        events_page = await get_events(session_id, after_cursor=last_cursor, limit=1000)
        for event in events_page.events:
            yield _sse_frame(event)
            last_cursor = event.cursor

        # Phase 2: Live stream via LISTEN/NOTIFY
        # Use a polling approach with LISTEN to avoid race conditions:
        # After subscribing, immediately check for events that arrived
        # between the end of Phase 1 and the LISTEN subscription.
        channel = f"session_{session_id.replace('-', '_')}"

        async for _payload in listen(channel):
            # On each notification (or initial subscription), fetch new events
            new_events = await get_events(session_id, after_cursor=last_cursor, limit=100)
            for event in new_events.events:
                yield _sse_frame(event)
                last_cursor = event.cursor

    return EventSourceResponse(event_generator())


# ---- Atlas B5 (FR-9.2) — filesystem browsing inside the sandbox ----


async def _load_session_for_filesystem(
    session_id: str, ctx: AuthContext
) -> tuple[uuid.UUID, dict]:
    """Shared preamble for the filesystem endpoints: parse the id, verify
    project scoping, and return (uid, session_row). Raises 404 when the
    session doesn't exist or belongs to another project."""
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )
    row = await fetch_one(
        "SELECT * FROM sessions WHERE id = $1 AND project_id = $2",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Session {session_id} not found"},
        )
    return uid, row


def _require_filesystem_target(session: dict, path: str | None) -> str:
    """Validate the query parameters both filesystem endpoints share: a
    session with a live container and an absolute in-container path."""
    container_id = session.get("container_id")
    if not container_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no_container",
                "message": "session has no running container to browse",
            },
        )
    if not path or not path.startswith("/"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": "path must be an absolute path inside the container",
            },
        )
    return container_id


@router.get("/{session_id}/filesystem")
async def list_session_filesystem(
    request: Request,
    session_id: str,
    path: str = Query(default="/"),
    ctx: AuthContext = Depends(require_auth),
):
    """List a directory inside the sandbox (Atlas B5, FR-9.2).

    Each entry carries name/type/size/permissions/owner/group/modified (and
    link_target for symlinks), as parsed from ``ls -la`` output by
    ``sandbox.list_dir``.
    """
    _, session = await _load_session_for_filesystem(session_id, ctx)
    container_id = _require_filesystem_target(session, path)
    try:
        # Atlas C3 / M4 — resolve the runtime pinned on the session metadata.
        sandbox = await resolve_session_sandbox(request.app.state, session)
        entries = await sandbox.list_dir(container_id, path)
    except SandboxError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "filesystem_error", "message": str(exc)},
        )
    return {"session_id": session_id, "path": path, "entries": entries}


@router.get("/{session_id}/filesystem/raw")
async def read_session_filesystem_raw(
    request: Request,
    session_id: str,
    path: str = Query(...),
    ctx: AuthContext = Depends(require_auth),
) -> Response:
    """Download a file from the sandbox as raw bytes (Atlas B5, FR-9.2).

    Binary-safe ``docker cp``-style read via ``sandbox.copy_from_container``;
    served as an attachment with an RFC 5987-encoded filename.
    """
    _, session = await _load_session_for_filesystem(session_id, ctx)
    container_id = _require_filesystem_target(session, path)
    try:
        # Atlas C3 / M4 — resolve the runtime pinned on the session metadata.
        sandbox = await resolve_session_sandbox(request.app.state, session)
        data = await sandbox.copy_from_container(container_id, path)
    except SandboxError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "filesystem_error", "message": str(exc)},
        )

    filename = os.path.basename(path.rstrip("/")) or "download"
    quoted = urllib.parse.quote(filename, safe="")
    disposition = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quoted}'
    return Response(
        content=data,
        media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )
