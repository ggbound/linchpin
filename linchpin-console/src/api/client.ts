const API_KEY_STORAGE_KEY = 'linchpin_api_key';

const BASE_URL: string =
  import.meta.env.VITE_API_URL || 'http://localhost:8000';

// ---------------------------------------------------------------------------
// API key helpers
// ---------------------------------------------------------------------------

export function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE_KEY);
}

export function setApiKey(key: string): void {
  localStorage.setItem(API_KEY_STORAGE_KEY, key);
}

export function clearApiKey(): void {
  localStorage.removeItem(API_KEY_STORAGE_KEY);
}

// ---------------------------------------------------------------------------
// Structured API error
// ---------------------------------------------------------------------------

export interface ApiError {
  status: number;
  error: string;
  message: string;
  details?: unknown;
}

/** One field-level validation error extracted from a 422 envelope. */
export interface FieldValidationError {
  /** Field path relative to the request body, e.g. "name" or "model.id". */
  path: string;
  /** Human-readable message, e.g. "Field required". */
  msg: string;
}

/**
 * Extract field-level errors from a 422 validation envelope.
 * The API wraps pydantic errors as { detail: { errors: [{ loc, msg, ... }] } }.
 */
export function extractValidationErrors(err: unknown): FieldValidationError[] {
  if (!err || typeof err !== 'object') return [];
  const apiErr = err as ApiError;
  if (apiErr.status !== 422) return [];
  const detail = (apiErr.details as { detail?: { errors?: unknown } } | undefined)?.detail;
  const errors = detail?.errors;
  if (!Array.isArray(errors)) return [];
  const out: FieldValidationError[] = [];
  for (const item of errors) {
    const e = item as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(e.loc) || typeof e.msg !== 'string') continue;
    const segments = e.loc.map(String).filter((s) => s !== 'body');
    out.push({ path: segments.join('.') || 'request', msg: e.msg });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function authHeaders(): Record<string, string> {
  const key = getApiKey();
  if (!key) return {};
  return { Authorization: `Bearer ${key}` };
}

function buildUrl(path: string, params?: Record<string, string>): string {
  const url = new URL(path, BASE_URL);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      url.searchParams.set(k, v);
    }
  }
  return url.toString();
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    clearApiKey();
    window.location.href = '/';
    throw { status: 401, error: 'Unauthorized', message: 'Invalid or missing API key' } as ApiError;
  }

  if (!res.ok) {
    let body: Record<string, unknown> = {};
    try {
      body = await res.json();
    } catch {
      // response may not be JSON
    }
    throw {
      status: res.status,
      error: body.error ?? res.statusText,
      message: (body.message ?? body.detail ?? res.statusText) as string,
      details: body.details ?? body,
    } as ApiError;
  }

  // 204 No Content
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

/** Fetch wrapper that converts network failures into a structured ApiError. */
async function doFetch(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw {
      status: 0,
      error: 'NetworkError',
      message: 'Connection error — unable to reach the API',
    } as ApiError;
  }
}

// ---------------------------------------------------------------------------
// ApiClient
// ---------------------------------------------------------------------------

export const apiClient = {
  async get<T>(path: string, params?: Record<string, string>): Promise<T> {
    const res = await doFetch(buildUrl(path, params), {
      method: 'GET',
      headers: { ...authHeaders() },
    });
    return handleResponse<T>(res);
  },

  async post<T>(path: string, body: unknown): Promise<T> {
    const res = await doFetch(buildUrl(path), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: JSON.stringify(body),
    });
    return handleResponse<T>(res);
  },

  async patch<T>(path: string, body: unknown): Promise<T> {
    const res = await doFetch(buildUrl(path), {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: JSON.stringify(body),
    });
    return handleResponse<T>(res);
  },

  async delete<T>(path: string, params?: Record<string, string>): Promise<T> {
    const res = await doFetch(buildUrl(path, params), {
      method: 'DELETE',
      headers: { ...authHeaders() },
    });
    return handleResponse<T>(res);
  },

  /** Multipart upload (e.g. skill bundles). `fieldName` is the form key. */
  async upload<T>(path: string, file: File, fieldName = 'bundle'): Promise<T> {
    const form = new FormData();
    form.append(fieldName, file);
    const res = await doFetch(buildUrl(path), {
      method: 'POST',
      headers: { ...authHeaders() }, // no Content-Type — browser sets boundary
      body: form,
    });
    return handleResponse<T>(res);
  },

  /** Binary download (file/artifact content, raw filesystem entries). */
  async download(path: string, params?: Record<string, string>): Promise<Blob> {
    const res = await doFetch(buildUrl(path, params), {
      method: 'GET',
      headers: { ...authHeaders() },
    });
    if (res.status === 401) {
      clearApiKey();
      window.location.href = '/';
      throw { status: 401, error: 'Unauthorized', message: 'Invalid or missing API key' } as ApiError;
    }
    if (!res.ok) {
      let body: Record<string, unknown> = {};
      try {
        body = await res.json();
      } catch {
        // response may not be JSON
      }
      throw {
        status: res.status,
        error: body.error ?? res.statusText,
        message: (body.message ?? body.detail ?? res.statusText) as string,
        details: body.details ?? body,
      } as ApiError;
    }
    return res.blob();
  },
};
