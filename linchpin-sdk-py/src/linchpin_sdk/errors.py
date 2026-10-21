"""Error hierarchy for the Linchpin SDK (FR-12.1 / unified envelope §6.4).

The API returns 4xx/5xx bodies as ``{"error": <code>, "message": ...,
"detail": ...}`` (main.py _error_envelope). error_from_status maps those
onto typed subclasses; legacy shapes (``{"detail": "..."}`` and plain
text) are tolerated so the SDK never crashes on error bodies.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

__all__ = [
    "LinchpinError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
    "RateLimitedError",
    "QuotaExceededError",
    "ServerError",
    "APIError",
    "NetworkError",
    "error_from_status",
]


class LinchpinError(Exception):
    """Base error carrying the HTTP status, machine code and raw detail."""

    def __init__(self, status: int, code: str, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.detail = detail


class AuthenticationError(LinchpinError):  # 401
    pass


class PermissionDeniedError(LinchpinError):  # 403
    pass


class NotFoundError(LinchpinError):  # 404
    pass


class ConflictError(LinchpinError):  # 409
    pass


class ValidationError(LinchpinError):  # 422
    pass


class RateLimitedError(LinchpinError):  # 429
    pass


class QuotaExceededError(LinchpinError):  # 402-style billing gates
    pass


class ServerError(LinchpinError):  # 5xx
    pass


class APIError(LinchpinError):  # any other non-2xx
    pass


class NetworkError(LinchpinError):
    """The request never reached a response — transport-level failure."""

    def __init__(self, cause: BaseException | object = None) -> None:
        super().__init__(0, "network_error", f"Request failed: {_describe(cause)}", None)
        # Mirrors ``raise ... from cause`` for callers that inspect __cause__.
        if isinstance(cause, BaseException):
            self.__cause__ = cause
            self.__suppress_context__ = True


def _describe(cause: Any) -> str:
    return str(cause)


_STATUS_TYPES: Dict[int, Tuple[type, ...]] = {
    401: (AuthenticationError,),
    402: (QuotaExceededError,),
    403: (PermissionDeniedError,),
    404: (NotFoundError,),
    409: (ConflictError,),
    422: (ValidationError,),
    429: (RateLimitedError,),
}


def error_from_status(status: int, body: Any) -> LinchpinError:
    """Map an HTTP status + parsed (or raw) error body onto a subclass.

    ``body`` may be a dict, a string, or None.
    """
    code, message, detail = _extract_envelope(body)
    error_type = _STATUS_TYPES.get(status)
    if error_type is None:
        error_type = (ServerError,) if status >= 500 else (APIError,)
    return error_type[0](status, code, message, detail)


def _extract_envelope(body: Any) -> Tuple[str, str, Any]:
    if isinstance(body, dict):
        # Unified envelope: {"error", "message", "detail"}
        if isinstance(body.get("error"), str) or isinstance(body.get("message"), str):
            return (
                body["error"] if isinstance(body.get("error"), str) else "unknown_error",
                body["message"] if isinstance(body.get("message"), str) else "Request failed",
                body.get("detail"),
            )
        # FastAPI default: {"detail": [{"loc", "msg", ...}]} or {"detail": "..."}
        if "detail" in body:
            return "unknown_error", _summarize_detail(body["detail"]), body.get("detail")

    if isinstance(body, str) and body.strip() != "":
        return "unknown_error", body, None

    return "unknown_error", "Request failed", None


def _summarize_detail(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, (list, tuple)):
        parts = "; ".join(
            str(item.get("msg")) if isinstance(item, dict) and "msg" in item else str(item)
            for item in detail
        )
        if parts:
            return parts
    return "Request failed"
