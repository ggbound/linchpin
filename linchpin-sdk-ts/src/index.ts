/**
 * linchpin-sdk — TypeScript SDK for the Linchpin self-hosted AI agent
 * runtime (FR-12.1). Zero runtime dependencies.
 */

export { LinchpinClient } from "./client.js";
export type { RequestOptions } from "./client.js";
export {
  SessionsResource,
  DeploymentsResource,
  ArtifactsResource,
  UsageResource,
  AuditResource,
} from "./client.js";

export {
  LinchpinError,
  AuthenticationError,
  PermissionDeniedError,
  NotFoundError,
  ConflictError,
  ValidationError,
  RateLimitedError,
  QuotaExceededError,
  ServerError,
  APIError,
  NetworkError,
  errorFromStatus,
} from "./errors.js";

export { SseParser, streamSse, streamNdjson } from "./sse.js";

export type {
  LinchpinClientOptions,
  Paginated,
  PaginatedEvents,
  ListParams,
  EventListParams,
  Session,
  CreateSessionParams,
  SpawnThreadParams,
  Event,
  EventPayloadInput,
  Deployment,
  CreateDeploymentParams,
  UpdateDeploymentParams,
  Artifact,
  ArtifactListParams,
  UsageParams,
  UsageResponse,
  UsageBucket,
  AuditParams,
  AuditEntry,
  SseEvent,
  SessionStreamEvent,
  StreamOptions,
} from "./types.js";
