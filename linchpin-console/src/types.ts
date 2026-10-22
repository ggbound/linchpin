// ---------------------------------------------------------------------------
// Model / Tool / MCP configuration
// ---------------------------------------------------------------------------

/** LLM provider configuration. */
export interface ModelConfig {
  provider: "openrouter" | "ollama";
  id: string;
  base_url?: string;
}

/** A single tool inside a builtin toolset entry. */
export interface BuiltinToolItemConfig {
  name: string;
  permission_policy: "always_allow" | "always_ask";
  enabled: boolean;
}

/** Built-in toolset entry (e.g. bash, read, write). */
export interface BuiltinToolConfig {
  type: "builtin";
  default_config: Record<string, unknown>;
  configs: BuiltinToolItemConfig[];
}

/** Custom tool entry with an HTTP endpoint. */
export interface CustomToolConfig {
  type: "custom";
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  permission_policy: "always_allow" | "always_ask";
  endpoint?: string;
}

/** Discriminated union on the `type` field. */
export type ToolConfig = BuiltinToolConfig | CustomToolConfig;

/** MCP server subprocess configuration. */
export interface MCPServerConfig {
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Agent
// ---------------------------------------------------------------------------

/** Sub-agent spawned via the multi-agent tool (FR-6.1). */
export interface SubagentConfig {
  name: string;
  agent_id: string;
  environment_id?: string | null;
  description?: string | null;
}

/** Agent domain model. */
export interface Agent {
  id: string;
  name: string;
  version: number;
  model: ModelConfig;
  system: string;
  tools: ToolConfig[];
  mcp_servers: MCPServerConfig[];
  skills: string[];
  multi_agents: SubagentConfig[];
  created_at: string;
  description?: string | null;
  metadata: Record<string, unknown>;
  archived_at?: string | null;
}

/** Frozen agent snapshot from GET /v1/agents/{id}/versions. */
export interface AgentVersion {
  agent_id: string;
  version: number;
  name: string;
  description?: string | null;
  metadata: Record<string, unknown>;
  model: ModelConfig;
  system: string;
  tools: ToolConfig[];
  mcp_servers: MCPServerConfig[];
  skills: string[];
  multi_agents: SubagentConfig[];
  snapshotted_at: string;
}

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------

/** Networking mode — discriminated union on `type` (three tiers, FR-4.3). */
export interface NoneNetworking {
  type: "none";
}

/** Limited networking with an explicit egress allowlist (Squid proxy). */
export interface LimitedNetworking {
  type: "limited";
  allowed_hosts: string[];
  allow_mcp_servers: boolean;
  allow_package_managers: boolean;
}

export interface UnrestrictedNetworking {
  type: "unrestricted";
}

export type NetworkingConfig =
  | NoneNetworking
  | LimitedNetworking
  | UnrestrictedNetworking;

/** Sandbox spec tier (FR-4.5): 1 vCPU / 2 GB, 2 vCPU / 4 GB, 4 vCPU / 8 GB. */
export type SandboxResourceTier = "1c2g" | "2c4g" | "4c8g";

/** Sandbox resource spec for session containers. */
export interface SandboxResources {
  tier: SandboxResourceTier;
}

/** Per-package-manager pre-install lists (baked into a derived image). */
export interface EnvironmentPackages {
  apt: string[];
  pip: string[];
  npm: string[];
  cargo: string[];
  gem: string[];
  go: string[];
}

/** Sandbox isolation runtime. */
export type SandboxIsolation = "docker" | "microvm";

/** Environment configuration wrapper. */
export interface EnvironmentConfig {
  networking: NetworkingConfig;
  packages: EnvironmentPackages;
  resources: SandboxResources;
  isolation: SandboxIsolation;
  tool_search: { enabled: boolean; threshold: number; max_results: number } | null;
  executor_node: string | null;
}

/** Environment domain model. */
export interface Environment {
  id: string;
  name: string;
  config: EnvironmentConfig;
  created_at: string;
  archived_at?: string | null;
}

/** Executor node (FR-4.6) — remote Docker host registered by a daemon. */
export interface ExecutorNode {
  id: string;
  org_id: string;
  name: string;
  url: string;
  prefix: string;
  max_sessions: number;
  status: "online" | "offline";
  last_heartbeat_at: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------

export type SessionStatus =
  | "running"
  | "idle"
  | "rescheduling"
  | "terminated"
  | "failed";

/** Aggregate counters for a session. */
export interface SessionStats {
  total_events: number;
  tool_calls: number;
  model_turns: number;
}

/** Token usage for a session. */
export interface SessionUsage {
  input_tokens: number;
  output_tokens: number;
}

// --- Session resource mounts (Atlas B5 / v0.5) -----------------------------

export type SessionResourceType =
  | "file"
  | "memory_store"
  | "github_repository"
  | "git_repository"
  | "vault";

export type SessionResourceState =
  | "mounted"
  | "unmounting"
  | "unmounted"
  | "failed";

/** Persisted resource-mount row on a session. */
export interface SessionResource {
  id: string;
  session_id: string;
  type: SessionResourceType;
  mount_path: string;
  config: Record<string, unknown>;
  state: SessionResourceState;
  error?: string | null;
  created_at: string;
  unmounted_at?: string | null;
}

/** Mount an uploaded file (source='upload' only) into the sandbox. */
export interface FileResourceConfig {
  type: "file";
  file_id: string;
  mount_path?: string | null;
}

/** Attach a memory store (v0.3 — reserved). */
export interface MemoryStoreResourceConfig {
  type: "memory_store";
  memory_store_id: string;
  access?: "read_only" | "read_write";
  instructions?: string | null;
}

/** Clone any HTTPS git remote into the sandbox. */
export interface GitRepositoryResourceConfig {
  type: "git_repository";
  url: string;
  mount_path: string;
  authorization_token?: string | null;
  branch?: string | null;
  shallow?: boolean;
}

/** Attach a credential vault (replaces legacy vault_ids). */
export interface VaultResourceConfig {
  type: "vault";
  vault_id: string;
}

/** Discriminated union on `type` — declared at session-create time. */
export type SessionResourceConfig =
  | FileResourceConfig
  | MemoryStoreResourceConfig
  | GitRepositoryResourceConfig
  | VaultResourceConfig;

/** Session domain model. */
export interface Session {
  id: string;
  agent_id: string;
  agent_version: number;
  environment_id: string;
  status: SessionStatus;
  stop_reason?: string | null;
  retry_count?: number;
  container_id?: string | null;
  title?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
  last_event_cursor?: string | null;
  ttl_seconds?: number | null;
  vault_ids: string[];
  resources: SessionResource[];
  outcome?: Record<string, unknown> | null;
  parent_session_id?: string | null;
  deployment_id?: string | null;
  events_encrypted?: boolean;
  stats: SessionStats;
  usage: SessionUsage;
}

// ---------------------------------------------------------------------------
// Event
// ---------------------------------------------------------------------------

export type EventType =
  | "user.message"
  | "user.steer"
  | "user.interrupt"
  | "user.tool_confirmation"
  | "user.custom_tool_result"
  | "agent.message"
  | "agent.message_delta"
  | "agent.thinking"
  | "agent.tool_use"
  | "agent.tool_result"
  | "agent.mcp_tool_use"
  | "agent.mcp_tool_result"
  | "agent.custom_tool_use"
  | "session.status_running"
  | "session.status_idle"
  | "session.status_rescheduled"
  | "session.status_terminated"
  | "session.error"
  | "session.requires_action";

/** Append-only event record. */
export interface SessionEvent {
  session_id: string;
  cursor: string;
  seq: number;
  type: EventType;
  payload: Record<string, unknown>;
  processed_at?: string;
}

/** SSE stream connection state (EventStreamPanel indicator). */
export type StreamState = "connecting" | "live" | "reconnecting" | "closed";

// ---------------------------------------------------------------------------
// Paginated responses
// ---------------------------------------------------------------------------

/** Generic cursor-paginated list for agents, environments, sessions, files. */
export interface PaginatedResponse<T> {
  data: T[];
  next_cursor?: string;
  has_more: boolean;
}

/** Cursor-paginated event list. */
export interface PaginatedEventsResponse {
  events: SessionEvent[];
  next_cursor?: string;
}

/** GET /v1/sessions/{id}/threads response. */
export interface ThreadListResponse {
  data: Session[];
}

/** Session sandbox filesystem listing (GET /v1/sessions/{id}/filesystem). */
export interface FilesystemEntry {
  name: string;
  type: "file" | "directory" | "symlink" | string;
  size: number;
  permissions: string;
  owner: string;
  group: string;
  modified: string | null;
  link_target?: string | null;
}

export interface FilesystemListing {
  session_id: string;
  path: string;
  entries: FilesystemEntry[];
}

// ---------------------------------------------------------------------------
// Files & Artifacts
// ---------------------------------------------------------------------------

export type FileSource = "upload" | "deliverable" | "artifact";

/** File resource (metadata only; bytes stream via /content). */
export interface FileRecord {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  source: FileSource;
  downloadable: boolean;
  scope_type?: "session" | null;
  scope_id?: string | null;
  created_at: string;
  archived_at?: string | null;
}

// ---------------------------------------------------------------------------
// Deployments (Atlas B7 — scheduled agent runs)
// ---------------------------------------------------------------------------

/** One event seeded into every session a deployment triggers. */
export interface InitialEventConfig {
  type: "user.message" | "user.steer";
  payload: Record<string, unknown>;
}

/** Deployment domain model. */
export interface Deployment {
  id: string;
  agent_id: string;
  environment_id: string;
  cron: string;
  timezone: string;
  initial_events: InitialEventConfig[];
  status: "paused" | "active";
  next_run_at?: string | null;
  created_at: string;
  archived_at?: string | null;
}

/** POST /v1/deployments request body. */
export interface CreateDeploymentRequest {
  agent_id: string;
  environment_id: string;
  cron: string;
  timezone?: string;
  initial_events?: InitialEventConfig[];
}

/** PATCH /v1/deployments/{id} request body. */
export interface UpdateDeploymentRequest {
  cron?: string;
  timezone?: string;
  initial_events?: InitialEventConfig[];
}

/** One grader evaluation from GET/POST /v1/sessions/{id}/outcome_evaluations. */
export interface OutcomeEvaluation {
  id: string;
  session_id: string;
  grader_id?: string | null;
  score: number;
  rationale?: string | null;
  criteria: Array<Record<string, unknown>>;
  created_at: string;
}

/** GET /v1/sessions/{id}/outcome_evaluations response. */
export interface OutcomeEvaluationsResponse {
  data: OutcomeEvaluation[];
}

/** POST /v1/sessions/{id}/outcome_evaluations request body. */
export interface PostOutcomeEvaluationRequest {
  score: number;
  rationale?: string | null;
  criteria?: Array<Record<string, unknown>>;
  grader_id?: string | null;
}

// ---------------------------------------------------------------------------
// Webhooks (outbound event delivery)
// ---------------------------------------------------------------------------

/** Supported webhook event types (WEBHOOK_RELEVANT_EVENTS). */
export type WebhookEventType =
  | "session.created"
  | "session.status_idle"
  | "session.terminated"
  | "session.failed"
  | "session.requires_action"
  | "session.status"
  | "session.error"
  | "session.status_terminated"
  | "vault.credential.created"
  | "vault.credential.updated"
  | "vault.credential.deleted";

/** Webhook endpoint. `secret` is set ONLY on the POST response. */
export interface WebhookEndpoint {
  id: string;
  url: string;
  enabled_events: string[];
  description?: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
  secret?: string | null;
}

/** POST /v1/webhook_endpoints request body. */
export interface CreateWebhookEndpointRequest {
  url: string;
  enabled_events?: string[];
  description?: string | null;
}

/** PATCH /v1/webhook_endpoints/{id} request body. */
export interface UpdateWebhookEndpointRequest {
  url?: string;
  enabled_events?: string[];
  description?: string | null;
  enabled?: boolean;
  rotate_secret?: boolean;
}

/** One delivery attempt record. */
export interface WebhookDelivery {
  id: string;
  endpoint_id: string;
  event_type: string;
  status: "pending" | "succeeded" | "failed" | "exhausted";
  attempts: number;
  last_attempt_at?: string | null;
  last_status_code?: number | null;
  last_error?: string | null;
  next_attempt_at: string;
  created_at: string;
  completed_at?: string | null;
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

/** Skill bundle row (bytes live on disk; row records SKILL.md frontmatter). */
export interface Skill {
  id: string;
  name: string;
  description: string;
  version?: string | null;
  bundle_sha256: string;
  bundle_size: number;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

/** GET /v1/skills response. */
export interface SkillListResponse {
  data: Skill[];
  has_more: boolean;
  next_cursor?: string | null;
}

/** POST /v1/skills/preflight response — bundle metadata without persisting. */
export interface SkillPreflightResponse {
  name: string;
  description: string;
  version?: string | null;
  /** The live skill a same-name upload would replace (upsert), if any. */
  replaces: Skill | null;
}

// ---------------------------------------------------------------------------
// Projects & API Keys (Atlas C1 — multi-tenancy admin)
// ---------------------------------------------------------------------------

/** A project (tenant) under the caller's organization. */
export interface Project {
  id: string;
  org_id: string;
  name: string;
  slug: string;
  max_concurrent_sessions?: number | null;
  monthly_token_budget?: number | null;
  created_at: string;
}

/** PATCH /v1/projects/{id} body — explicit null clears a limit. */
export interface UpdateProjectRequest {
  max_concurrent_sessions?: number | null;
  monthly_token_budget?: number | null;
}

/** POST /v1/api-keys request body. */
export interface CreateApiKeyRequest {
  name?: string;
  project_id?: string | null;
  scopes?: string[];
  rate_limit_rpm?: number | null;
}

/** API key metadata (never includes the secret material). */
export interface ApiKey {
  id: string;
  project_id: string;
  name: string;
  prefix: string;
  scopes: string[];
  status: string;
  rate_limit_rpm?: number | null;
  created_at: string;
  last_used_at?: string | null;
}

/** POST /v1/api-keys response — `key` plaintext, shown exactly once. */
export interface ApiKeyCreated extends ApiKey {
  key: string;
}

// ---------------------------------------------------------------------------
// Request models
// ---------------------------------------------------------------------------

/** POST /v1/agents request body. */
export interface CreateAgentRequest {
  name: string;
  model: ModelConfig;
  system: string;
  tools: ToolConfig[];
  mcp_servers: MCPServerConfig[];
  skills?: string[];
  multi_agents?: SubagentConfig[];
  description?: string | null;
  metadata?: Record<string, unknown>;
}

/** PATCH /v1/agents/{id} request body. */
export interface UpdateAgentRequest {
  name?: string;
  model?: ModelConfig;
  system?: string;
  skills?: string[];
  multi_agents?: SubagentConfig[];
  description?: string | null;
  metadata?: Record<string, unknown>;
}

/** POST /v1/environments request body. */
export interface CreateEnvironmentRequest {
  name: string;
  config: EnvironmentConfig;
}

/** POST /v1/sessions request body. */
export interface CreateSessionRequest {
  agent_id: string;
  environment_id: string;
  title?: string;
  ttl_seconds?: number;
  resources?: SessionResourceConfig[];
}

/** POST /v1/sessions/{id}/threads request body. */
export interface SpawnThreadRequest {
  agent_id: string;
  environment_id: string;
  title?: string;
  metadata?: Record<string, unknown>;
  ttl_seconds?: number;
  vault_ids?: string[];
  resources?: SessionResourceConfig[];
  input_message?: { content: string };
  outcome?: Record<string, unknown>;
}

/** POST /v1/sessions/{id}/events request body. */
export interface PostEventsRequest {
  events: Array<{
    type: EventType;
    payload: Record<string, unknown>;
  }>;
}

// ---------------------------------------------------------------------------
// Vault & Credential
// ---------------------------------------------------------------------------

export type CredentialType = "bearer_token" | "api_key" | "oauth" | "byok_kek";
export type ProviderName = "openrouter" | "ollama";

/** Vault domain model. */
export interface Vault {
  id: string;
  display_name: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

/** Credential response (secrets omitted). */
export interface CredentialResponse {
  id: string;
  vault_id: string;
  credential_type: CredentialType;
  mcp_server_url?: string | null;
  provider?: ProviderName | null;
  client_id?: string | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

/** POST /v1/vaults request body. */
export interface CreateVaultRequest {
  display_name: string;
  metadata?: Record<string, unknown>;
}

/** POST /v1/vaults/{vault_id}/credentials request body (union). */
export type CreateCredentialRequest =
  | { credential_type: "bearer_token"; mcp_server_url: string; token: string }
  | { credential_type: "api_key"; provider: ProviderName; api_key: string }
  | {
      credential_type: "oauth";
      mcp_server_url: string;
      access_token: string;
      refresh_token?: string;
      client_id?: string;
      client_secret?: string;
    }
  | { credential_type: "byok_kek"; kek: string };

/** PATCH /v1/vaults/{vault_id}/credentials/{credential_id} request body. */
export interface UpdateCredentialRequest {
  token?: string;
  api_key?: string;
  access_token?: string;
  refresh_token?: string;
  client_secret?: string;
  kek?: string;
}

// ---------------------------------------------------------------------------
// Usage
// ---------------------------------------------------------------------------

/** One usage bucket: a UTC day, a project, or the overall total. */
export interface UsageBucket {
  bucket: string;
  sessions: number;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
}

/** GET /v1/usage response. */
export interface UsageResponse {
  group_by: "day" | "project";
  from?: string | null;
  to?: string | null;
  total: UsageBucket;
  buckets: UsageBucket[];
}

// ---------------------------------------------------------------------------
// Memory Stores (v0.3.0)
// ---------------------------------------------------------------------------

/** Memory store domain model. */
export interface MemoryStore {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

/** One memory entry, addressed by path within a store. */
export interface Memory {
  id: string;
  memory_store_id: string;
  path: string;
  content_sha256: string;
  size_bytes: number;
  created_at: string;
  updated_at: string;
}

/** Append-only memory version snapshot. */
export interface MemoryVersion {
  id: string;
  memory_id: string;
  memory_store_id: string;
  seq: number;
  content_sha256: string;
  size_bytes: number;
  author: string;
  action: string;
  redacted_at: string | null;
  created_at: string;
  expires_at: string;
}

/** POST/PATCH /v1/memory_stores/{id}/memories request body. */
export interface WriteMemoryRequest {
  path: string;
  content: string;
  precondition?: { sha256: string | null } | null;
}

/** POST /v1/memory_stores/{id}/memory_versions/{ver}/redact request body. */
export interface RedactVersionRequest {
  reason: string;
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

/** One audit log record (GET /v1/audit NDJSON stream line). */
export interface AuditEntry {
  id: string;
  at: string;
  project_id: string | null;
  actor: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  detail: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Dreams
// ---------------------------------------------------------------------------

export type DreamStatus = "pending" | "running" | "completed" | "failed" | "canceled";

/** Which past sessions a Dream curates over (AND'd together). */
export interface DreamSessionFilter {
  agent_id?: string | null;
  since?: string | null;
  until?: string | null;
  limit?: number;
}

/** A single Dream record (v0.7.0 research preview). */
export interface Dream {
  id: string;
  input_memory_store_id: string;
  output_memory_store_id: string | null;
  output_store_name: string;
  dreamer_session_id: string | null;
  dreamer_agent_id: string | null;
  session_filter: DreamSessionFilter;
  status: DreamStatus;
  error: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

/** GET /v1/dreams response. */
export interface DreamListResponse {
  data: Dream[];
  has_more: boolean;
  next_cursor: string | null;
}

/** POST /v1/dreams request body. */
export interface CreateDreamRequest {
  input_memory_store_id: string;
  output_store_name: string;
  session_filter?: DreamSessionFilter;
  dreamer_agent_id?: string;
}
