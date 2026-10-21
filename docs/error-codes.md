# Error codes

Every 4xx/5xx response from the Linchpin API uses a single JSON envelope:

```json
{
  "error": "not_found",
  "message": "Agent 550e8400-... not found",
  "detail": null
}
```

| Field | Type | Always present | Notes |
|---|---|---|---|
| `error` | string | yes | Stable machine-readable code (see tables below) |
| `message` | string | yes | Human-readable summary, safe to log/show |
| `detail` | any | no | Structured extras (field errors, limit values, retry hints) |

The envelope is produced by the unified exception handlers in
`app/main.py`. Routes that raise `detail={"error": ..., "message": ...}`
keep their explicit code; anything else derives a code from the HTTP
status. The same shape is published as the `ErrorEnvelope` schema in
`GET /openapi.json`.

## 422 validation errors

Request-body validation (Pydantic) returns `error: "invalid_request"` with
the failing fields in `detail.errors`:

```json
{
  "error": "invalid_request",
  "message": "Request validation failed",
  "detail": {
    "errors": [
      {"type": "missing", "loc": ["body", "agent_id"], "msg": "Field required", "input": {}}
    ]
  }
}
```

## Rate limits & quotas (429)

Both 429 causes share the envelope but carry distinct codes and extras:

- `rate_limited` — per-API-key request rate exceeded (sliding 60s window).
  Response includes a `Retry-After` header (seconds); `detail.retry_after`
  mirrors it. See [docs/limits.md](limits.md).
- `quota_exceeded` — project quota exhausted before a session is created.
  `detail.limit_type` is `concurrent_sessions` (with `limit`, `active`) or
  `monthly_tokens` (with `limit`, `used`).

## Core codes

These are the HTTP-derived fallbacks and the codes used by the auth layer.

| Code | HTTP | Meaning |
|---|---|---|
| `invalid_request` | 400, 422 | Malformed request, unknown parameter, or validation failure |
| `unauthorized` | 401 | Missing `Authorization` header, bad `Bearer` format, or invalid API key |
| `forbidden` | 403 | Authenticated but missing a required scope, or calling a bootstrap-key-only endpoint (e.g. `PATCH /v1/projects/{id}`) with an issued key |
| `not_found` | 404 | Resource does not exist (or is not visible to this key's project) |
| `conflict` | 409 | State conflict — archived resource, concurrent modification, duplicate idempotency payload |
| `rate_limited` | 429 | Per-key request rate exceeded; obey `Retry-After` |
| `quota_exceeded` | 429 | Project concurrency or monthly-token quota exhausted |
| `internal` | 500 | Unexpected server error; nothing about the request needs to change |

## Domain codes

| Code | HTTP | Area | Meaning |
|---|---|---|---|
| `validation_error` | 400/422 | sessions, dreams, deployments, artifacts, memories | Semantic validation failed beyond field syntax (bad event payload, unsupported resource kind, …) |
| `unsupported_api_version` | 400 | versioning | `Linchpin-API-Version` header names an unknown version; `detail.supported` lists the valid ones |
| `precondition_failed` | 412 | memories | Compare-and-swap version mismatch; re-read and retry |
| `not_implemented` | 501 | session resources, mounts | Endpoint/feature is reserved but not implemented for this target |
| `invalid_path_prefix` | 422 | memories | Path escapes the store root or lacks the required prefix |
| `name_conflict` | 409 | memory stores, executor nodes | A store/node with that name already exists in the project |
| `memory_store_in_use` | 409 | memory stores | Store is referenced by agents/sessions and cannot be deleted |
| `output_store_name_taken` | 409 | dreams | Dream output store name already exists |
| `file_in_use` | 409 | files | File is mounted into a live session and cannot be deleted |
| `no_container` | 409 | session filesystem | Session has no running container to browse |
| `redacted` | 404 | memory versions | Version content was redacted and is no longer retrievable |
| `not_downloadable` | 403 | artifacts, files | Object exists but downloads are disallowed (e.g. uploads vs agent-produced deliverables) |
| `file_too_large` | 413 | files | Upload exceeds the byte cap; `detail` carries the limit |
| `skill_too_large` | 413 | skills | Skill bundle exceeds `SKILL_BUNDLE_MAX_BYTES` |
| `invalid_skill_bundle` | 422 | skills | Bundle zip unreadable or missing valid SKILL.md metadata |
| `invalid_event_type` | 422 | sessions | Event-type filter names an unknown event type |
| `duplicate_mount_source` | 422 | sessions | The same host path is mounted at multiple container paths |
| `thread_depth_exceeded` | 422 | sessions | Sub-thread nesting exceeds the configured maximum |
| `filesystem_error` | 400 | session filesystem | Sandbox refused the directory listing / file read |
| `git_clone_failed` | 500 | sessions | Workspace git clone failed during provisioning |
| `memory_materialize_failed` | 500 | sessions | Memory-store materialization failed during provisioning |
| `skill_materialize_failed` | 500 | sessions | Skill-bundle materialization failed during provisioning |
| `sandbox_image_build_failed` | 500 | sessions | Environment image build failed |
| `egress_proxy_failed` | 500 | sessions | Egress proxy setup failed |
| `sandbox_create_failed` | 500 | sessions | Container creation failed |
| `sandbox_runtime_unavailable` | 503 | sessions | No sandbox runtime can serve this environment right now |
| `sandbox_unavailable` | 503 | executor daemon | Sandbox handle missing for the requested container op |
| `executor_token_unconfigured` | 503 | executor daemon | Server has no executor token configured; node auth cannot work |
| `encryption_unavailable` | 503 | executor nodes | `VAULT_ENCRYPTION_KEY` missing/invalid; secret material cannot be stored or read |
| `storage_missing` | 500 | artifacts, files | Database row exists but the underlying bytes are gone from object storage |
| `dreamer_not_configured` | 422 | dreams | No dreamer agent configured (no default and none supplied) |

## Versioning note

Error envelopes are stable across `Linchpin-API-Version` values. New codes
may appear over time; treat unknown codes as their HTTP status implies
(`4xx` → client error, `5xx` → retry/inspect server state).
