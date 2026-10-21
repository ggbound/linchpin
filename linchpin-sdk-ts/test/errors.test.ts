import { describe, expect, it } from "vitest";

import {
  APIError,
  AuthenticationError,
  ConflictError,
  NetworkError,
  NotFoundError,
  PermissionDeniedError,
  QuotaExceededError,
  RateLimitedError,
  ServerError,
  ValidationError,
  errorFromStatus,
} from "../src/errors.js";

describe("errorFromStatus mapping", () => {
  const cases: Array<[number, new (...args: never[]) => Error]> = [
    [401, AuthenticationError],
    [402, QuotaExceededError],
    [403, PermissionDeniedError],
    [404, NotFoundError],
    [409, ConflictError],
    [422, ValidationError],
    [429, RateLimitedError],
    [500, ServerError],
    [503, ServerError],
    [418, APIError],
  ];

  for (const [status, expected] of cases) {
    it(`maps ${status} onto ${expected.name}`, () => {
      const error = errorFromStatus(status, null);
      expect(error).toBeInstanceOf(expected);
      expect(error).toBeInstanceOf(Error);
    });
  }
});

describe("envelope extraction", () => {
  it("reads the unified envelope {error, message, detail}", () => {
    const error = errorFromStatus(404, {
      error: "not_found",
      message: "Session abc not found",
      detail: null,
    });
    expect(error.status).toBe(404);
    expect(error.code).toBe("not_found");
    expect(error.message).toBe("Session abc not found");
  });

  it("keeps a structured detail", () => {
    const detail = { conflict: "environment_in_use" };
    const error = errorFromStatus(409, { error: "conflict", message: "busy", detail });
    expect(error.detail).toEqual(detail);
  });

  it("falls back to FastAPI's legacy {detail: string} shape", () => {
    const error = errorFromStatus(422, { detail: "Validation failed" });
    expect(error.code).toBe("unknown_error");
    expect(error.message).toBe("Validation failed");
  });

  it("summarizes FastAPI pydantic detail arrays", () => {
    const error = errorFromStatus(422, {
      detail: [{ loc: ["body", "agent_id"], msg: "field required" }],
    });
    expect(error.message).toContain("field required");
  });

  it("uses a raw text body as the message", () => {
    const error = errorFromStatus(500, "boom");
    expect(error).toBeInstanceOf(ServerError);
    expect(error.message).toBe("boom");
  });

  it("never throws on null bodies", () => {
    const error = errorFromStatus(502, null);
    expect(error.message).toBe("Request failed");
    expect(error.code).toBe("unknown_error");
  });
});

describe("NetworkError", () => {
  it("preserves the cause and carries status 0", () => {
    const cause = new TypeError("fetch failed");
    const error = new NetworkError(cause);
    expect(error.status).toBe(0);
    expect(error.code).toBe("network_error");
    expect(error.message).toContain("fetch failed");
    expect((error as { cause?: unknown }).cause).toBe(cause);
  });
});
