/**
 * Error hierarchy for the Linchpin SDK (FR-12.1 / unified envelope §6.4).
 *
 * The API returns 4xx/5xx bodies as ``{"error": <code>, "message": ...,
 * "detail": ...}`` (main.py _error_envelope). errorFromStatus maps those
 * onto typed subclasses; legacy shapes (``{"detail": "..."}`` and plain
 * text) are tolerated so the SDK never crashes on error bodies.
 */

export class LinchpinError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;

  constructor(status: number, code: string, message: string, detail: unknown = null) {
    super(message);
    this.name = new.target.name;
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export class AuthenticationError extends LinchpinError {} // 401
export class PermissionDeniedError extends LinchpinError {} // 403
export class NotFoundError extends LinchpinError {} // 404
export class ConflictError extends LinchpinError {} // 409
export class ValidationError extends LinchpinError {} // 422
export class RateLimitedError extends LinchpinError {} // 429
export class QuotaExceededError extends LinchpinError {} // 402-style billing gates
export class ServerError extends LinchpinError {} // 5xx
export class APIError extends LinchpinError {} // any other non-2xx

/** The request never reached a response — transport-level failure. */
export class NetworkError extends LinchpinError {
  constructor(cause: unknown) {
    super(0, "network_error", `Request failed: ${describe(cause)}`, null);
    (this as { cause?: unknown }).cause = cause;
  }
}

function describe(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  return String(cause);
}

/**
 * Map an HTTP status + parsed (or raw) error body onto a LinchpinError
 * subclass. ``body`` may be an object, a string, or null.
 */
export function errorFromStatus(status: number, body: unknown): LinchpinError {
  const { code, message, detail } = extractEnvelope(body);
  const args = [status, code, message, detail] as const;

  switch (status) {
    case 401:
      return new AuthenticationError(...args);
    case 402:
      return new QuotaExceededError(...args);
    case 403:
      return new PermissionDeniedError(...args);
    case 404:
      return new NotFoundError(...args);
    case 409:
      return new ConflictError(...args);
    case 422:
      return new ValidationError(...args);
    case 429:
      return new RateLimitedError(...args);
    default:
      if (status >= 500) return new ServerError(...args);
      return new APIError(...args);
  }
}

function extractEnvelope(body: unknown): {
  code: string;
  message: string;
  detail: unknown;
} {
  if (body !== null && typeof body === "object") {
    const obj = body as Record<string, unknown>;

    // Unified envelope: {"error", "message", "detail"}
    if (typeof obj.error === "string" || typeof obj.message === "string") {
      return {
        code: typeof obj.error === "string" ? obj.error : "unknown_error",
        message: typeof obj.message === "string" ? obj.message : "Request failed",
        detail: obj.detail ?? null,
      };
    }

    // FastAPI default: {"detail": [{"loc", "msg", ...}]} or {"detail": "..."}
    if ("detail" in obj) {
      return {
        code: "unknown_error",
        message: summarizeDetail(obj.detail),
        detail: obj.detail ?? null,
      };
    }
  }

  if (typeof body === "string" && body.trim() !== "") {
    return { code: "unknown_error", message: body, detail: null };
  }

  return { code: "unknown_error", message: "Request failed", detail: null };
}

function summarizeDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) =>
        item !== null && typeof item === "object" && "msg" in (item as Record<string, unknown>)
          ? String((item as Record<string, unknown>).msg)
          : String(item),
      )
      .join("; ");
    if (parts) return parts;
  }
  return "Request failed";
}
