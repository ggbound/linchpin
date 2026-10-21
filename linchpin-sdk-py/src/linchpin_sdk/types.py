"""Linchpin SDK — resource and request/response types (FR-12.1).

Types mirror the FastAPI models in ``linchpin-api/app/models.py`` but are
intentionally loose: resources are plain dicts at runtime (``TypedDict``
is advisory only), so the SDK keeps working when the server adds fields.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

try:  # Python >= 3.11
    from typing import Required, TypedDict
except ImportError:  # pragma: no cover - 3.10 fallback
    from typing_extensions import Required, TypedDict  # type: ignore[no-redef]

__all__ = [
    "Artifact",
    "ArtifactListParams",
    "AuditEntry",
    "AuditParams",
    "CreateDeploymentParams",
    "CreateSessionParams",
    "Deployment",
    "Event",
    "EventListParams",
    "EventPayloadInput",
    "LinchpinClientOptions",
    "ListParams",
    "Paginated",
    "PaginatedEvents",
    "Session",
    "SessionStreamEvent",
    "SessionStreamPayload",
    "SpawnThreadParams",
    "SseEvent",
    "StreamOptions",
    "UpdateDeploymentParams",
    "UsageBucket",
    "UsageParams",
    "UsageResponse",
]


# ---------------------------------------------------------------------------
# Client options
# ---------------------------------------------------------------------------


class LinchpinClientOptions(TypedDict, total=False):
    """Keyword options for :class:`linchpin_sdk.LinchpinClient`."""

    base_url: Required[str]
    """Base URL of the Linchpin API, e.g. ``https://linchpin.example.com``."""

    api_key: Required[str]
    """API key sent as ``Authorization: Bearer <api_key>``."""

    headers: Dict[str, str]
    """Extra headers attached to every request."""

    timeout: Optional[float]
    """Per-request timeout in seconds (``None`` = no timeout)."""

    transport: Any
    """Custom transport callable; defaults to stdlib ``urllib``.

    Signature: ``(method, url, headers, body, timeout) -> HttpResponse``.
    """


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class Paginated(TypedDict, total=False):
    """Generic cursor-paginated list (``PaginatedListResponse``)."""

    data: Required[List[Any]]
    next_cursor: Optional[str]
    has_more: Optional[bool]


class PaginatedEvents(TypedDict, total=False):
    """Cursor-paginated event list (``PaginatedEventsResponse``)."""

    events: Required[List["Event"]]
    next_cursor: Optional[str]


class ListParams(TypedDict, total=False):
    limit: int
    offset: int


class EventListParams(TypedDict, total=False):
    after_cursor: str
    limit: int


# ---------------------------------------------------------------------------
# Sessions (FR-2 / Atlas A2 / v0.6 threads)
# ---------------------------------------------------------------------------


class Session(TypedDict, total=False):
    id: Required[str]
    agent_id: Required[str]
    agent_version: Required[int]
    environment_id: Required[str]
    status: Required[str]
    stop_reason: Optional[str]
    retry_count: Optional[int]
    container_id: Optional[str]
    title: Optional[str]
    metadata: Optional[Dict[str, Any]]
    created_at: Optional[str]
    updated_at: Optional[str]
    archived_at: Optional[str]
    last_event_cursor: Optional[str]
    ttl_seconds: Optional[int]
    vault_ids: Optional[List[str]]
    parent_session_id: Optional[str]
    deployment_id: Optional[str]
    events_encrypted: Optional[bool]


class CreateSessionParams(TypedDict, total=False):
    agent_id: Required[str]
    environment_id: Required[str]
    title: Optional[str]
    metadata: Optional[Dict[str, Any]]
    ttl_seconds: Optional[int]
    vault_ids: Optional[List[str]]
    resources: Optional[List[Any]]
    outcome: Optional[Any]


class SpawnThreadParams(TypedDict, total=False):
    """POST /v1/sessions/{parent_id}/threads — spawn a worker thread
    handled by a (possibly different) agent (FR-6.1 subagent orchestration).
    """

    agent_id: Required[str]
    environment_id: Optional[str]
    title: Optional[str]
    metadata: Optional[Dict[str, Any]]
    ttl_seconds: Optional[int]
    vault_ids: Optional[List[str]]
    resources: Optional[List[Any]]
    input_message: Optional[Union[str, Dict[str, Any], List[Any]]]
    outcome: Optional[Any]


class Event(TypedDict, total=False):
    session_id: Required[str]
    cursor: Required[str]
    seq: Required[int]
    type: Required[str]
    payload: Optional[Dict[str, Any]]
    processed_at: Optional[str]
    event_id: Optional[str]


class EventPayloadInput(TypedDict, total=False):
    """Item of the ``POST /v1/sessions/{id}/events`` batch envelope."""

    type: Required[str]
    payload: Optional[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Deployments (Atlas B7)
# ---------------------------------------------------------------------------


class Deployment(TypedDict, total=False):
    id: Required[str]
    agent_id: Required[str]
    environment_id: Required[str]
    cron: Required[str]
    timezone: Required[str]
    status: Required[str]
    next_run_at: Optional[str]
    created_at: Optional[str]
    archived_at: Optional[str]


class CreateDeploymentParams(TypedDict, total=False):
    agent_id: Required[str]
    environment_id: Required[str]
    cron: Required[str]
    timezone: Optional[str]
    initial_events: Optional[List[EventPayloadInput]]


class UpdateDeploymentParams(TypedDict, total=False):
    cron: Optional[str]
    timezone: Optional[str]
    initial_events: Optional[List[EventPayloadInput]]


# ---------------------------------------------------------------------------
# Artifacts / usage / audit
# ---------------------------------------------------------------------------


class Artifact(TypedDict, total=False):
    id: Required[str]
    session_id: Optional[str]
    path: Optional[str]
    size_bytes: Optional[int]
    created_at: Optional[str]


class ArtifactListParams(ListParams, total=False):
    session_id: str


class UsageParams(TypedDict, total=False):
    group_by: str
    from_: str
    """Sent as the ``from`` query parameter."""
    to: str


class UsageBucket(TypedDict, total=False):
    bucket: Required[str]
    sessions: Optional[int]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cache_creation_input_tokens: Optional[int]
    cache_read_input_tokens: Optional[int]


class UsageResponse(TypedDict, total=False):
    group_by: Required[str]
    from_: Optional[str]
    to: Optional[str]
    total: Required[UsageBucket]
    buckets: Optional[List[UsageBucket]]


class AuditParams(TypedDict, total=False):
    from_: str
    """Sent as the ``from`` query parameter."""
    to: str
    resource_type: str
    action: str
    limit: int


class AuditEntry(TypedDict, total=False):
    at: Optional[str]
    project_id: Optional[str]
    actor: Optional[str]
    action: Optional[str]
    resource_type: Optional[str]
    resource_id: Optional[str]
    detail: Optional[Dict[str, Any]]


# ---------------------------------------------------------------------------
# SSE streaming (Atlas A3)
# ---------------------------------------------------------------------------


class SseEvent:
    """One parsed SSE frame (WHATWG field names)."""

    __slots__ = ("id", "event", "data")

    def __init__(self, event: str, data: str, id: Optional[str] = None) -> None:
        self.id = id
        self.event = event
        self.data = data

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SseEvent):
            return NotImplemented
        return (self.id, self.event, self.data) == (other.id, other.event, other.data)

    def __hash__(self) -> bool:  # keep hashable despite __eq__
        return hash((self.id, self.event, self.data))

    def __repr__(self) -> str:
        return f"SseEvent(id={self.id!r}, event={self.event!r}, data={self.data!r})"


class SessionStreamPayload(TypedDict, total=False):
    """Server envelope of a Linchpin stream frame's ``data`` JSON."""

    session_id: Required[str]
    type: Required[str]
    cursor: Required[str]
    seq: Required[int]
    event_id: Optional[str]
    payload: Optional[Dict[str, Any]]
    processed_at: Optional[str]


class SessionStreamEvent:
    """Decoded Linchpin stream frame: an SSE event plus the parsed envelope."""

    __slots__ = ("id", "event", "data", "payload")

    def __init__(
        self,
        event: str,
        data: str,
        id: Optional[str] = None,
        payload: Optional[SessionStreamPayload] = None,
    ) -> None:
        self.id = id
        self.event = event
        self.data = data
        self.payload = payload

    def __repr__(self) -> str:
        return (
            f"SessionStreamEvent(id={self.id!r}, event={self.event!r}, "
            f"payload={self.payload!r})"
        )


class StreamOptions(TypedDict, total=False):
    """Options for :meth:`SessionsResource.stream`."""

    cursor: Optional[str]
    """Raw cursor query param — ignored when ``last_event_id`` is set."""

    last_event_id: Optional[str]
    """Sent as ``Last-Event-ID`` (takes priority over ``cursor`` server-side)."""

    headers: Optional[Dict[str, str]]
