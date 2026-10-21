# Rate limits & quotas

Linchpin (Atlas M5, FR-11.2/11.3) enforces two kinds of limits at the API
layer, both opt-in — every limit column is nullable and `NULL` means
**unlimited**:

| Limit | Scope | Storage | Enforced at |
|---|---|---|---|
| Requests per minute | API key | `api_keys.rate_limit_rpm` | Auth layer, every `/v1` request |
| Concurrent sessions | Project | `projects.max_concurrent_sessions` | `POST /v1/sessions` and `POST /v1/sessions/{parent_id}/threads` |
| Monthly token budget | Project | `projects.monthly_token_budget` | Same two endpoints |

Schema: migration `0029_quotas_and_rate_limits`. All three columns are
`INTEGER`/`BIGINT`, nullable, with no backfill — existing deployments keep
behaving exactly as before until you set values.

## Configuring limits

### Per-key request rate

Set `rate_limit_rpm` when issuing a key:

```bash
curl -sX POST http://localhost:8000/v1/api-keys \
  -H "Authorization: Bearer $LINCHPIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"ci-bot","rate_limit_rpm":120}'
```

The **bootstrap key** (the `LINCHPIN_API_KEY` env value) is never rate
limited — it exists to administer the system, including issuing keys.

Implementation: in-process sliding window over the last 60 seconds, keyed
by API key id. Rejected requests do not consume a slot, so a client that
backs off on `Retry-After` recovers immediately once the window drains.

### Project quotas

Two quotas protect a project from runaway agent spend. They are
bootstrap-key-only and managed via `PATCH /v1/projects/{id}`:

```bash
# Set both quotas
curl -sX PATCH http://localhost:8000/v1/projects/<project_id> \
  -H "Authorization: Bearer $LINCHPIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"max_concurrent_sessions": 10, "monthly_token_budget": 50000000}'

# Clear one (back to unlimited)
curl -sX PATCH http://localhost:8000/v1/projects/<project_id> \
  -H "Authorization: Bearer $LINCHPIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"max_concurrent_sessions": null}'
```

PATCH semantics are JSON-Merge-Patch-like:

- Field **omitted** → unchanged
- Field **`null`** → cleared (unlimited)
- Field with a **value ≥ 1** → set

Both quotas are checked before any side effect of session creation. A
session that idempotently replays an earlier create is not re-charged.
Counting semantics:

- **Concurrent sessions** — sessions in a live state (`status` not
  `terminated`/`failed`, not archived).
- **Monthly tokens** — sum of `input + output + cache_creation +
  cache_read` tokens recorded in `sessions.usage` for sessions created in
  the current UTC calendar month.

Quota checks are best-effort against the database: if the DB is
unreachable, session creation **fails open** (the request proceeds) rather
than taking the whole API down.

## Response contract

Both limits surface as HTTP 429 with the unified error envelope
([docs/error-codes.md](error-codes.md)):

```json
// rate_limited — also carries a Retry-After header (seconds)
{"error": "rate_limited", "message": "Rate limit exceeded: 120 requests per minute", "detail": {"retry_after": 13}}

// quota_exceeded — limit_type is concurrent_sessions or monthly_tokens
{"error": "quota_exceeded", "message": "Concurrent session limit reached (10/10 active)", "detail": {"limit_type": "concurrent_sessions", "limit": 10, "active": 10}}
```

`429` from quota checks does **not** carry `Retry-After` — retrying
immediately will fail again until sessions finish or the month rolls over.

## Multi-replica semantics (honest ceiling)

The in-process rate limiter is per-replica: with N API replicas behind a
load balancer, the effective ceiling for a key is **N × rate_limit_rpm**
requests/minute (each replica keeps its own window; there is no shared
state). Project quotas live in Postgres and are exact for concurrency and
eventually exact for tokens.

For a hard global rate limit, enforce it at the gateway instead. Example
APISIX recipe (`limit-count` plugin, keyed on the bearer token, 60s
window) as a rough equivalent of 120 rpm:

```yaml
plugins:
  limit-count:
    count: 120
    time_window: 60
    key_type: var
    key: http_authorization
    rejected_code: 429
    policy: redis          # cluster-wide counter; drop for single-node apisix
    redis_host: 127.0.0.1
    redis_port: 6379
    redis_timeout: 1000
```

Notes for gateway deployments:

- Keep the app-level `rate_limit_rpm` as a cheap first line; the gateway
  counter is the authoritative global ceiling.
- Keying on the raw `Authorization` header groups bootstrap + issued keys
  by their token value, which is what you want.
- IP allowlisting / geo rules remain gateway-level concerns; Linchpin does
  not implement them.

## Cache and change propagation

API key rows (including `rate_limit_rpm`) are cached in-process for 60
seconds. Changes to a key's limit take effect within that TTL. Project
quota changes via `PATCH` are read from the DB at session-create time and
take effect immediately.
