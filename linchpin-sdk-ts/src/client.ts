/**
 * LinchpinClient — zero-dependency HTTP client for the Linchpin API
 * (FR-12.1).
 *
 * Uses the global ``fetch`` (Node >= 18, all modern browsers, workers).
 * Resources are exposed as ``client.sessions`` / ``client.deployments`` /
 * ``client.artifacts`` / ``client.usage`` / ``client.audit``.
 */

import {
  APIError,
  NetworkError,
  errorFromStatus,
} from "./errors.js";
import { streamNdjson, streamSse } from "./sse.js";
import type {
  Artifact,
  ArtifactListParams,
  AuditEntry,
  AuditParams,
  CreateDeploymentParams,
  CreateSessionParams,
  Deployment,
  Event,
  EventListParams,
  EventPayloadInput,
  LinchpinClientOptions,
  ListParams,
  Paginated,
  PaginatedEvents,
  Session,
  SessionStreamEvent,
  SseEvent,
  SpawnThreadParams,
  StreamOptions,
  UpdateDeploymentParams,
  UsageParams,
  UsageResponse,
} from "./types.js";

type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  query?: Record<string, QueryValue>;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/** URL-encode a path segment. */
function seg(value: string): string {
  return encodeURIComponent(value);
}

function parseJsonOrNull(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function readBodyText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}

export class LinchpinClient {
  readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly extraHeaders: Record<string, string>;
  private readonly fetchImpl: typeof fetch;

  readonly sessions: SessionsResource;
  readonly deployments: DeploymentsResource;
  readonly artifacts: ArtifactsResource;
  readonly usage: UsageResource;
  readonly audit: AuditResource;

  constructor(options: LinchpinClientOptions) {
    if (!options.baseUrl) throw new TypeError("LinchpinClient: baseUrl is required");
    if (!options.apiKey) throw new TypeError("LinchpinClient: apiKey is required");

    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.extraHeaders = options.headers ?? {};
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);

    this.sessions = new SessionsResource(this);
    this.deployments = new DeploymentsResource(this);
    this.artifacts = new ArtifactsResource(this);
    this.usage = new UsageResource(this);
    this.audit = new AuditResource(this);
  }

  // ------------------------------------------------------------------
  // Transport
  // ------------------------------------------------------------------

  private buildHeaders(extra?: Record<string, string>, hasBody = false): Record<string, string> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      ...this.extraHeaders,
      ...extra,
    };
    if (hasBody) headers["Content-Type"] = "application/json";
    return headers;
  }

  private buildUrl(path: string, query?: Record<string, QueryValue>): URL {
    const url = new URL(this.baseUrl + path);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value === undefined || value === null) continue;
        url.searchParams.set(key, String(value));
      }
    }
    return url;
  }

  private async send(
    method: string,
    path: string,
    options: RequestOptions = {},
  ): Promise<Response> {
    const url = this.buildUrl(path, options.query);
    const init: RequestInit = {
      method,
      headers: this.buildHeaders(options.headers, options.body !== undefined),
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    };
    try {
      return await this.fetchImpl(url, init);
    } catch (cause) {
      throw new NetworkError(cause);
    }
  }

  /** JSON request → parsed body (``undefined`` for 204/empty). */
  async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const response = await this.send(method, path, options);
    if (!response.ok) {
      throw errorFromStatus(response.status, parseJsonOrNull(await readBodyText(response)));
    }
    const text = await readBodyText(response);
    if (!text) return undefined as T;
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new APIError(
        response.status,
        "invalid_response",
        "Response body is not valid JSON",
        text,
      );
    }
  }

  /** Raw request returning the untouched ``Response`` (for streaming). */
  async rawRequest(method: string, path: string, options: RequestOptions = {}): Promise<Response> {
    const response = await this.send(method, path, options);
    if (!response.ok) {
      throw errorFromStatus(response.status, parseJsonOrNull(await readBodyText(response)));
    }
    return response;
  }

  /** Generic SSE consumer for any streaming endpoint. */
  async *stream(path: string, options: RequestOptions = {}): AsyncGenerator<SseEvent> {
    const response = await this.rawRequest("GET", path, options);
    yield* streamSse(response);
  }
}

// ---------------------------------------------------------------------------
// /v1/sessions
// ---------------------------------------------------------------------------

export class SessionsResource {
  constructor(private readonly client: LinchpinClient) {}

  create(params: CreateSessionParams, options: RequestOptions = {}): Promise<Session> {
    return this.client.request<Session>("POST", "/v1/sessions", { ...options, body: params });
  }

  get(sessionId: string, options: RequestOptions = {}): Promise<Session> {
    return this.client.request<Session>("GET", `/v1/sessions/${seg(sessionId)}`, options);
  }

  list(params: ListParams = {}, options: RequestOptions = {}): Promise<Paginated<Session>> {
    return this.client.request<Paginated<Session>>("GET", "/v1/sessions", {
      ...options,
      query: params as Record<string, QueryValue>,
    });
  }

  /** POST /v1/sessions/{id} — partial update (title / metadata / status). */
  update(sessionId: string, body: Record<string, unknown>, options: RequestOptions = {}): Promise<Session> {
    return this.client.request<Session>("POST", `/v1/sessions/${seg(sessionId)}`, {
      ...options,
      body,
    });
  }

  /** DELETE /v1/sessions/{id} — terminate the session. */
  delete(sessionId: string, options: RequestOptions = {}): Promise<Session> {
    return this.client.request<Session>("DELETE", `/v1/sessions/${seg(sessionId)}`, options);
  }

  archive(sessionId: string, options: RequestOptions = {}): Promise<Session> {
    return this.client.request<Session>("POST", `/v1/sessions/${seg(sessionId)}/archive`, options);
  }

  listEvents(
    sessionId: string,
    params: EventListParams = {},
    options: RequestOptions = {},
  ): Promise<PaginatedEvents> {
    return this.client.request<PaginatedEvents>("GET", `/v1/sessions/${seg(sessionId)}/events`, {
      ...options,
      query: params as Record<string, QueryValue>,
    });
  }

  postEvents(
    sessionId: string,
    events: EventPayloadInput[],
    options: RequestOptions = {},
  ): Promise<Event[]> {
    return this.client.request<Event[]>("POST", `/v1/sessions/${seg(sessionId)}/events`, {
      ...options,
      body: { events },
    });
  }

  listThreads(parentId: string, options: RequestOptions = {}): Promise<{ data: Session[] }> {
    return this.client.request<{ data: Session[] }>(
      "GET",
      `/v1/sessions/${seg(parentId)}/threads`,
      options,
    );
  }

  spawnThread(parentId: string, params: SpawnThreadParams, options: RequestOptions = {}): Promise<Session> {
    return this.client.request<Session>("POST", `/v1/sessions/${seg(parentId)}/threads`, {
      ...options,
      body: params,
    });
  }

  /**
   * Stream session events over SSE (GET /v1/sessions/{id}/stream).
   *
   * Pass ``lastEventId`` (the ``id:`` of the last frame seen) to resume
   * exactly where a previous stream stopped — the server prioritizes it
   * over ``cursor``.
   */
  async *stream(
    sessionId: string,
    options: StreamOptions = {},
  ): AsyncGenerator<SessionStreamEvent> {
    const headers: Record<string, string> = { ...options.headers };
    if (options.lastEventId !== undefined) headers["Last-Event-ID"] = options.lastEventId;

    const response = await this.client.rawRequest(
      "GET",
      `/v1/sessions/${seg(sessionId)}/stream`,
      { query: { cursor: options.cursor }, headers, signal: options.signal },
    );

    for await (const frame of streamSse(response)) {
      let payload: SessionStreamEvent["payload"];
      try {
        payload = JSON.parse(frame.data) as SessionStreamEvent["payload"];
      } catch {
        payload = undefined;
      }
      yield { id: frame.id, event: frame.event, data: frame.data, payload };
    }
  }
}

// ---------------------------------------------------------------------------
// /v1/deployments
// ---------------------------------------------------------------------------

export class DeploymentsResource {
  constructor(private readonly client: LinchpinClient) {}

  create(params: CreateDeploymentParams, options: RequestOptions = {}): Promise<Deployment> {
    return this.client.request<Deployment>("POST", "/v1/deployments", { ...options, body: params });
  }

  get(deploymentId: string, options: RequestOptions = {}): Promise<Deployment> {
    return this.client.request<Deployment>("GET", `/v1/deployments/${seg(deploymentId)}`, options);
  }

  list(params: ListParams = {}, options: RequestOptions = {}): Promise<Paginated<Deployment>> {
    return this.client.request<Paginated<Deployment>>("GET", "/v1/deployments", {
      ...options,
      query: params as Record<string, QueryValue>,
    });
  }

  update(
    deploymentId: string,
    params: UpdateDeploymentParams,
    options: RequestOptions = {},
  ): Promise<Deployment> {
    return this.client.request<Deployment>("PATCH", `/v1/deployments/${seg(deploymentId)}`, {
      ...options,
      body: params,
    });
  }

  pause(deploymentId: string, options: RequestOptions = {}): Promise<Deployment> {
    return this.client.request<Deployment>("POST", `/v1/deployments/${seg(deploymentId)}/pause`, options);
  }

  resume(deploymentId: string, options: RequestOptions = {}): Promise<Deployment> {
    return this.client.request<Deployment>("POST", `/v1/deployments/${seg(deploymentId)}/resume`, options);
  }

  delete(deploymentId: string, options: RequestOptions = {}): Promise<Deployment> {
    return this.client.request<Deployment>("DELETE", `/v1/deployments/${seg(deploymentId)}`, options);
  }
}

// ---------------------------------------------------------------------------
// /v1/artifacts
// ---------------------------------------------------------------------------

export class ArtifactsResource {
  constructor(private readonly client: LinchpinClient) {}

  list(params: ArtifactListParams = {}, options: RequestOptions = {}): Promise<Paginated<Artifact>> {
    return this.client.request<Paginated<Artifact>>("GET", "/v1/artifacts", {
      ...options,
      query: params as Record<string, QueryValue>,
    });
  }

  get(artifactId: string, options: RequestOptions = {}): Promise<Artifact> {
    return this.client.request<Artifact>("GET", `/v1/artifacts/${seg(artifactId)}`, options);
  }

  /** Raw artifact content as text. */
  async content(artifactId: string, options: RequestOptions = {}): Promise<string> {
    const response = await this.client.rawRequest(
      "GET",
      `/v1/artifacts/${seg(artifactId)}/content`,
      options,
    );
    return readBodyText(response);
  }
}

// ---------------------------------------------------------------------------
// /v1/usage
// ---------------------------------------------------------------------------

export class UsageResource {
  constructor(private readonly client: LinchpinClient) {}

  get(params: UsageParams = {}, options: RequestOptions = {}): Promise<UsageResponse> {
    return this.client.request<UsageResponse>("GET", "/v1/usage", {
      ...options,
      query: params as Record<string, QueryValue>,
    });
  }
}

// ---------------------------------------------------------------------------
// /v1/audit — NDJSON export (FR-10.4)
// ---------------------------------------------------------------------------

export class AuditResource {
  constructor(private readonly client: LinchpinClient) {}

  /** Stream audit entries as parsed NDJSON records (newest first). */
  async *export(
    params: AuditParams = {},
    options: RequestOptions = {},
  ): AsyncGenerator<AuditEntry> {
    const response = await this.client.rawRequest("GET", "/v1/audit", {
      ...options,
      query: params as Record<string, QueryValue>,
    });
    for await (const record of streamNdjson(response)) {
      yield record as AuditEntry;
    }
  }
}
