"""LinchpinClient — zero-dependency HTTP client for the Linchpin API
(FR-12.1).

Uses the stdlib ``urllib`` stack by default and is fully synchronous.
Resources are exposed as ``client.sessions`` / ``client.deployments`` /
``client.artifacts`` / ``client.usage`` / ``client.audit``. A custom
``transport`` callable can be injected for testing or alternate HTTP
stacks.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import (
    Any,
    Dict,
    Iterator,
    Optional,
    TypedDict,
    Union,
)

from .errors import APIError, LinchpinError, NetworkError, error_from_status
from .sse import stream_ndjson, stream_sse
from .types import (
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
)

__all__ = [
    "HttpResponse",
    "RequestOptions",
    "LinchpinClient",
    "SessionsResource",
    "DeploymentsResource",
    "ArtifactsResource",
    "UsageResource",
    "AuditResource",
]

QueryValue = Union[str, int, float, bool, None]


class RequestOptions(TypedDict, total=False):
    """Per-call overrides mirroring the TS SDK's ``RequestOptions``."""

    query: Dict[str, QueryValue]
    body: Any
    headers: Dict[str, str]


class HttpResponse:
    """Minimal response surface returned by a transport.

    ``raw_request`` returns this object so callers can stream the body
    (via :func:`linchpin_sdk.sse.stream_sse` / ``stream_ndjson``, which
    only need ``iter_bytes``) or read it manually.
    """

    def __init__(self, status: int, headers: Dict[str, str], stream: Any) -> None:
        self.status = status
        self.headers = headers
        self._stream = stream

    def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)

    def iter_bytes(self, chunk_size: int = 8192) -> Iterator[bytes]:
        while True:
            chunk = self._stream.read(chunk_size)
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if close is not None:
            try:
                close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    def __enter__(self) -> "HttpResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _urllib_transport(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[str],
    timeout: Optional[float],
) -> HttpResponse:
    """Default transport: stdlib ``urllib.request`` (sync, zero-dependency)."""
    data = body.encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # HTTPError is file-like and carries the error body — keep it
        # reachable so error_from_status can extract the envelope.
        return HttpResponse(exc.code, dict(exc.headers.items()), exc)
    except (urllib.error.URLError, OSError) as exc:
        raise NetworkError(exc) from exc
    return HttpResponse(response.status, dict(response.headers.items()), response)


def seg(value: str) -> str:
    """URL-encode a path segment."""
    return urllib.parse.quote(str(value), safe="")


def _parse_json_or_null(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _read_body_text(response: HttpResponse) -> str:
    try:
        data = response.read()
    except Exception:
        return ""
    return data.decode("utf-8", "replace")


def _with_from_alias(params: Optional[Dict[str, QueryValue]]) -> Dict[str, QueryValue]:
    """Rename the ``from_`` Python kwarg to the API's ``from`` query key.

    Preserves the original key order so generated URLs match the TS SDK.
    """
    return {"from" if key == "from_" else key: value for key, value in (params or {}).items()}


class LinchpinClient:
    """Synchronous Linchpin API client (zero runtime dependencies)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = 60.0,
        transport: Any = None,
    ) -> None:
        if not base_url:
            raise TypeError("LinchpinClient: base_url is required")
        if not api_key:
            raise TypeError("LinchpinClient: api_key is required")

        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._extra_headers = dict(headers or {})
        self._timeout = timeout
        self._transport = transport or _urllib_transport

        self.sessions = SessionsResource(self)
        self.deployments = DeploymentsResource(self)
        self.artifacts = ArtifactsResource(self)
        self.usage = UsageResource(self)
        self.audit = AuditResource(self)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _build_headers(
        self, extra: Optional[Dict[str, str]] = None, has_body: bool = False
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {"Authorization": f"Bearer {self._api_key}"}
        headers.update(self._extra_headers)
        if extra:
            headers.update(extra)
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _build_url(
        self, path: str, query: Optional[Dict[str, QueryValue]] = None
    ) -> str:
        url = self.base_url + path
        pairs = []
        if query:
            for key, value in query.items():
                if value is None:
                    continue
                if isinstance(value, bool):
                    # Mirror TS String(true) === "true", not Python's "True".
                    value = "true" if value else "false"
                pairs.append((key, str(value)))
        if pairs:
            url += "?" + urllib.parse.urlencode(pairs)
        return url

    def _send(self, method: str, path: str, options: RequestOptions) -> HttpResponse:
        url = self._build_url(path, options.get("query"))
        body = options.get("body")
        body_text = json.dumps(body) if body is not None else None
        headers = self._build_headers(options.get("headers"), has_body=body_text is not None)
        try:
            return self._transport(method, url, headers, body_text, self._timeout)
        except LinchpinError:
            raise
        except Exception as exc:
            raise NetworkError(exc) from exc

    def request(
        self, method: str, path: str, options: Optional[RequestOptions] = None
    ) -> Any:
        """JSON request -> parsed body (``None`` for 204/empty)."""
        response = self._send(method, path, options or {})
        with response:
            if not 200 <= response.status < 300:
                raise error_from_status(
                    response.status, _parse_json_or_null(_read_body_text(response))
                )
            text = _read_body_text(response)
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            raise APIError(
                response.status,
                "invalid_response",
                "Response body is not valid JSON",
                text,
            ) from None

    def raw_request(
        self, method: str, path: str, options: Optional[RequestOptions] = None
    ) -> HttpResponse:
        """Raw request returning the untouched response (for streaming)."""
        response = self._send(method, path, options or {})
        if not 200 <= response.status < 300:
            try:
                text = _read_body_text(response)
            finally:
                response.close()
            raise error_from_status(response.status, _parse_json_or_null(text))
        return response

    def stream(
        self, path: str, options: Optional[RequestOptions] = None
    ) -> Iterator[SseEvent]:
        """Generic SSE consumer for any streaming endpoint."""
        response = self.raw_request("GET", path, options)
        yield from stream_sse(response)


# ---------------------------------------------------------------------------
# /v1/sessions
# ---------------------------------------------------------------------------


class SessionsResource:
    def __init__(self, client: LinchpinClient) -> None:
        self._client = client

    def create(
        self, params: CreateSessionParams, options: Optional[RequestOptions] = None
    ) -> Session:
        return self._client.request(
            "POST", "/v1/sessions", {**(options or {}), "body": params}
        )

    def get(self, session_id: str, options: Optional[RequestOptions] = None) -> Session:
        return self._client.request("GET", f"/v1/sessions/{seg(session_id)}", options)

    def list(
        self, params: Optional[ListParams] = None, options: Optional[RequestOptions] = None
    ) -> Paginated:
        return self._client.request(
            "GET", "/v1/sessions", {**(options or {}), "query": dict(params or {})}
        )

    def update(
        self,
        session_id: str,
        body: Dict[str, Any],
        options: Optional[RequestOptions] = None,
    ) -> Session:
        """POST /v1/sessions/{id} — partial update (title / metadata / status)."""
        return self._client.request(
            "POST", f"/v1/sessions/{seg(session_id)}", {**(options or {}), "body": body}
        )

    def delete(self, session_id: str, options: Optional[RequestOptions] = None) -> Session:
        """DELETE /v1/sessions/{id} — terminate the session."""
        return self._client.request("DELETE", f"/v1/sessions/{seg(session_id)}", options)

    def archive(self, session_id: str, options: Optional[RequestOptions] = None) -> Session:
        return self._client.request(
            "POST", f"/v1/sessions/{seg(session_id)}/archive", options
        )

    def list_events(
        self,
        session_id: str,
        params: Optional[EventListParams] = None,
        options: Optional[RequestOptions] = None,
    ) -> PaginatedEvents:
        return self._client.request(
            "GET",
            f"/v1/sessions/{seg(session_id)}/events",
            {**(options or {}), "query": dict(params or {})},
        )

    def post_events(
        self,
        session_id: str,
        events: list,
        options: Optional[RequestOptions] = None,
    ) -> list:
        return self._client.request(
            "POST",
            f"/v1/sessions/{seg(session_id)}/events",
            {**(options or {}), "body": {"events": events}},
        )

    def list_threads(
        self, parent_id: str, options: Optional[RequestOptions] = None
    ) -> Dict[str, Any]:
        return self._client.request("GET", f"/v1/sessions/{seg(parent_id)}/threads", options)

    def spawn_thread(
        self,
        parent_id: str,
        params: SpawnThreadParams,
        options: Optional[RequestOptions] = None,
    ) -> Session:
        return self._client.request(
            "POST",
            f"/v1/sessions/{seg(parent_id)}/threads",
            {**(options or {}), "body": params},
        )

    def stream(
        self, session_id: str, options: Optional[StreamOptions] = None
    ) -> Iterator[SessionStreamEvent]:
        """Stream session events over SSE (GET /v1/sessions/{id}/stream).

        Pass ``last_event_id`` (the ``id:`` of the last frame seen) to
        resume exactly where a previous stream stopped — the server
        prioritizes it over ``cursor``.
        """
        opts: StreamOptions = options or {}
        headers = dict(opts.get("headers") or {})
        last_event_id = opts.get("last_event_id")
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id

        response = self._client.raw_request(
            "GET",
            f"/v1/sessions/{seg(session_id)}/stream",
            {"query": {"cursor": opts.get("cursor")}, "headers": headers},
        )

        for frame in stream_sse(response):
            try:
                payload = json.loads(frame.data)
            except ValueError:
                payload = None
            yield SessionStreamEvent(
                id=frame.id, event=frame.event, data=frame.data, payload=payload
            )


# ---------------------------------------------------------------------------
# /v1/deployments
# ---------------------------------------------------------------------------


class DeploymentsResource:
    def __init__(self, client: LinchpinClient) -> None:
        self._client = client

    def create(
        self, params: CreateDeploymentParams, options: Optional[RequestOptions] = None
    ) -> Deployment:
        return self._client.request(
            "POST", "/v1/deployments", {**(options or {}), "body": params}
        )

    def get(self, deployment_id: str, options: Optional[RequestOptions] = None) -> Deployment:
        return self._client.request("GET", f"/v1/deployments/{seg(deployment_id)}", options)

    def list(
        self, params: Optional[ListParams] = None, options: Optional[RequestOptions] = None
    ) -> Paginated:
        return self._client.request(
            "GET", "/v1/deployments", {**(options or {}), "query": dict(params or {})}
        )

    def update(
        self,
        deployment_id: str,
        params: UpdateDeploymentParams,
        options: Optional[RequestOptions] = None,
    ) -> Deployment:
        return self._client.request(
            "PATCH",
            f"/v1/deployments/{seg(deployment_id)}",
            {**(options or {}), "body": params},
        )

    def pause(self, deployment_id: str, options: Optional[RequestOptions] = None) -> Deployment:
        return self._client.request(
            "POST", f"/v1/deployments/{seg(deployment_id)}/pause", options
        )

    def resume(self, deployment_id: str, options: Optional[RequestOptions] = None) -> Deployment:
        return self._client.request(
            "POST", f"/v1/deployments/{seg(deployment_id)}/resume", options
        )

    def delete(self, deployment_id: str, options: Optional[RequestOptions] = None) -> Deployment:
        return self._client.request("DELETE", f"/v1/deployments/{seg(deployment_id)}", options)


# ---------------------------------------------------------------------------
# /v1/artifacts
# ---------------------------------------------------------------------------


class ArtifactsResource:
    def __init__(self, client: LinchpinClient) -> None:
        self._client = client

    def list(
        self, params: Optional[ArtifactListParams] = None, options: Optional[RequestOptions] = None
    ) -> Paginated:
        return self._client.request(
            "GET", "/v1/artifacts", {**(options or {}), "query": dict(params or {})}
        )

    def get(self, artifact_id: str, options: Optional[RequestOptions] = None) -> Artifact:
        return self._client.request("GET", f"/v1/artifacts/{seg(artifact_id)}", options)

    def content(self, artifact_id: str, options: Optional[RequestOptions] = None) -> str:
        """Raw artifact content as text."""
        response = self._client.raw_request(
            "GET", f"/v1/artifacts/{seg(artifact_id)}/content", options
        )
        with response:
            return _read_body_text(response)


# ---------------------------------------------------------------------------
# /v1/usage
# ---------------------------------------------------------------------------


class UsageResource:
    def __init__(self, client: LinchpinClient) -> None:
        self._client = client

    def get(
        self, params: Optional[UsageParams] = None, options: Optional[RequestOptions] = None
    ) -> UsageResponse:
        return self._client.request(
            "GET",
            "/v1/usage",
            {**(options or {}), "query": _with_from_alias(dict(params or {}))},
        )


# ---------------------------------------------------------------------------
# /v1/audit — NDJSON export (FR-10.4)
# ---------------------------------------------------------------------------


class AuditResource:
    def __init__(self, client: LinchpinClient) -> None:
        self._client = client

    def export(
        self, params: Optional[AuditParams] = None, options: Optional[RequestOptions] = None
    ) -> Iterator[AuditEntry]:
        """Stream audit entries as parsed NDJSON records (newest first)."""
        response = self._client.raw_request(
            "GET",
            "/v1/audit",
            {**(options or {}), "query": _with_from_alias(dict(params or {}))},
        )
        yield from stream_ndjson(response)
