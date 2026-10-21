/**
 * Linchpin SDK — shared resource and request/response types (FR-12.1).
 *
 * Types mirror the FastAPI models in linchpin-api/app/models.py but are
 * intentionally loose: every resource carries an index signature so the
 * SDK keeps working when the server adds fields.
 */

// ---------------------------------------------------------------------------
// Client options
// ---------------------------------------------------------------------------

export interface LinchpinClientOptions {
  /** Base URL of the Linchpin API, e.g. ``https://linchpin.example.com``. */
  baseUrl: string;
  /** API key sent as ``Authorization: Bearer <apiKey>``. */
  apiKey: string;
  /** Extra headers attached to every request. */
  headers?: Record<string, string>;
  /** Custom fetch implementation (defaults to the global ``fetch``). */
  fetch?: typeof fetch;
}

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------

/** Generic offset/cursor-paginated list (PaginatedListResponse). */
export interface Paginated<T> {
  data: T[];
  next_cursor?: string | null;
  has_more?: boolean;
}

/** Cursor-paginated event list (PaginatedEventsResponse). */
export interface PaginatedEvents {
  events: Event[];
  next_cursor?: string | null;
}

export interface ListParams {
  limit?: number;
  offset?: number;
}

export interface EventListParams {
  after_cursor?: string;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Sessions (FR-2 / Atlas A2 / v0.6 threads)
// ---------------------------------------------------------------------------

export interface Session {
  id: string;
  agent_id: string;
  agent_version: number;
  environment_id: string;
  status: string;
  stop_reason?: string | null;
  retry_count?: number;
  container_id?: string | null;
  title?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  archived_at?: string | null;
  last_event_cursor?: string | null;
  ttl_seconds?: number | null;
  vault_ids?: string[];
  parent_session_id?: string | null;
  deployment_id?: string | null;
  events_encrypted?: boolean;
  [key: string]: unknown;
}

export interface CreateSessionParams {
  agent_id: string;
  environment_id: string;
  title?: string | null;
  metadata?: Record<string, unknown>;
  ttl_seconds?: number | null;
  vault_ids?: string[];
  resources?: unknown[];
  outcome?: unknown;
}

/**
 * POST /v1/sessions/{parent_id}/threads — spawn a worker thread handled
 * by a (possibly different) agent (FR-6.1 subagent orchestration).
 */
export interface SpawnThreadParams {
  agent_id: string;
  environment_id?: string | null;
  title?: string | null;
  metadata?: Record<string, unknown>;
  ttl_seconds?: number | null;
  vault_ids?: string[];
  resources?: unknown[];
  input_message?: string | Record<string, unknown> | unknown[] | null;
  outcome?: unknown;
}

export interface Event {
  session_id: string;
  cursor: string;
  seq: number;
  type: string;
  payload?: Record<string, unknown>;
  processed_at?: string | null;
  event_id?: string | null;
  [key: string]: unknown;
}

/** Item of the POST /v1/sessions/{id}/events batch envelope. */
export interface EventPayloadInput {
  type: string;
  payload?: Record<string, unknown>;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Deployments (Atlas B)
// ---------------------------------------------------------------------------

export interface Deployment {
  id: string;
  agent_id: string;
  environment_id: string;
  cron: string;
  timezone: string;
  status: "paused" | "active";
  next_run_at?: string | null;
  created_at?: string;
  archived_at?: string | null;
  [key: string]: unknown;
}

export interface CreateDeploymentParams {
  agent_id: string;
  environment_id: string;
  cron: string;
  timezone?: string;
  initial_events?: EventPayloadInput[];
}

export interface UpdateDeploymentParams {
  cron?: string;
  timezone?: string;
  initial_events?: EventPayloadInput[];
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Artifacts / usage / audit
// ---------------------------------------------------------------------------

export interface Artifact {
  id: string;
  session_id?: string;
  path?: string;
  size_bytes?: number;
  created_at?: string;
  [key: string]: unknown;
}

export interface ArtifactListParams extends ListParams {
  session_id?: string;
}

export interface UsageParams {
  group_by?: "day" | "project";
  from?: string;
  to?: string;
}

export interface UsageBucket {
  [key: string]: unknown;
}

export interface UsageResponse {
  group_by: "day" | "project";
  from?: string | null;
  to?: string | null;
  total: UsageBucket;
  buckets?: UsageBucket[];
}

export interface AuditParams {
  from?: string;
  to?: string;
  resource_type?: string;
  action?: string;
  limit?: number;
}

export interface AuditEntry {
  at?: string;
  project_id?: string | null;
  actor?: string | null;
  action?: string;
  resource_type?: string;
  resource_id?: string | null;
  detail?: Record<string, unknown>;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// SSE streaming (Atlas A3)
// ---------------------------------------------------------------------------

/** One parsed SSE frame (WHATWG field names). */
export interface SseEvent {
  /** ``id:`` field — the per-session ``seq`` on Linchpin streams. */
  id?: string;
  /** ``event:`` field — defaults to ``"message"`` per the SSE spec. */
  event: string;
  /** Raw ``data:`` payload (multi-line data joined with ``\n``). */
  data: string;
}

/** Decoded Linchpin stream frame: an SSE event plus the parsed envelope. */
export interface SessionStreamEvent extends SseEvent {
  /**
   * ``JSON.parse(frame.data)`` when parseable — the server envelope is
   * ``{session_id, type, cursor, seq, event_id, payload, processed_at}``.
   */
  payload?: {
    session_id?: string;
    type?: string;
    cursor?: string;
    seq?: number;
    event_id?: string;
    payload?: Record<string, unknown>;
    processed_at?: string;
    [key: string]: unknown;
  };
}

export interface StreamOptions {
  /** Raw cursor query param — ignored when ``lastEventId`` is set. */
  cursor?: string;
  /** Sent as ``Last-Event-ID`` (takes priority over ``cursor`` server-side). */
  lastEventId?: string;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}
