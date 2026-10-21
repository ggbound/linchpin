"""OpenTelemetry tracing hooks for model requests (Atlas B10).

Dual-write strategy: the orchestrator already emits `span.model_request_start`
/ `span.model_request_end` events into the session event log (consumed by SSE
clients and the API). This module mirrors those spans into OpenTelemetry so
they land in any OTLP collector (e.g. Langfuse) without changing the
event-log contract.

Trace continuity: the OTel trace_id is derived from the session id
(UUID -> 128-bit int), so every span of a session shares one trace.
Hierarchy:

    agent.turn        one iteration of the orchestrator main loop
      model_request   one model call (paired via the events-table span_id)

Every span carries `session.id` / `langfuse.session.id` attributes so
backends can group spans by conversation. Turn spans are closed lazily:
`start_turn` ends the previous turn's span, and `end_session_tracing` closes
the current one plus any model spans left open by a failure path.

All hooks are best-effort — a tracing failure is logged and swallowed, never
propagated into the agent loop. Disabled by default
(`LINCHPIN_OTEL_ENABLED=false`): hooks are zero-cost no-ops (`_tracer None`).

Configuration (os.environ):

- LINCHPIN_OTEL_ENABLED       "1"/"true"/"yes"/"on" to enable (default: off)
- LINCHPIN_OTLP_ENDPOINT      OTLP/HTTP traces endpoint
                              (default: http://localhost:4318/v1/traces)
- LINCHPIN_OTLP_HEADERS       comma-separated "Key=Value" pairs, e.g. Langfuse
                              basic auth: "Authorization=Basic <base64>"
- LINCHPIN_OTEL_SERVICE_NAME  resource service.name (default: linchpin-api)
- LINCHPIN_OTLP_TIMEOUT_SEC   OTLP export timeout in seconds (default: 5)
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    Status,
    StatusCode,
    TraceFlags,
)

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger("linchpin-api.tracing")

_DEFAULT_OTLP_ENDPOINT = "http://localhost:4318/v1/traces"
_DEFAULT_SERVICE_NAME = "linchpin-api"
_DEFAULT_OTLP_TIMEOUT = 5.0

# Module-level tracer/provider — deliberately NOT the global
# opentelemetry.set_tracer_provider(), which can only be set once per process
# and would make repeated activate/deactivate (tests) impossible.
_provider: "TracerProvider | None" = None
_tracer: trace.Tracer | None = None

# session_id -> open turn span + in-flight model spans
_sessions: dict[str, "_SessionTracing"] = {}


@dataclass
class _SessionTracing:
    """Open OTel state for one running session."""

    ctx: Context
    turn_span: Span | None = None
    model_spans: dict[str, Span] = field(default_factory=dict)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse "K1=V1,K2=V2" into a dict (values may contain '=' padding)."""
    headers: dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip():
            headers[key.strip()] = value.strip()
    return headers


def _session_context(session_id: str) -> Context:
    """Detached context pinning the session-derived trace_id.

    trace_id = UUID(session_id).int (128-bit) — one trace per session; the
    placeholder NonRecordingSpan supplies it to every span started under
    this context.
    """
    span_context = SpanContext(
        trace_id=uuid.UUID(session_id).int,
        span_id=uuid.uuid4().int & 0xFFFFFFFFFFFFFFFF,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_context))


def _session_state(session_id: str) -> _SessionTracing:
    state = _sessions.get(session_id)
    if state is None:
        state = _SessionTracing(ctx=_session_context(session_id))
        _sessions[session_id] = state
    return state


# ---------------------------------------------------------------------------
# Orchestrator hooks (all best-effort; no-op when tracing is disabled)
# ---------------------------------------------------------------------------


def start_turn(session_id: str) -> None:
    """Open the agent.turn span for a loop iteration (closes the previous one)."""
    if _tracer is None:
        return
    try:
        state = _session_state(session_id)
        if state.turn_span is not None:
            state.turn_span.end()
            state.turn_span = None
        state.turn_span = _tracer.start_span(
            "agent.turn",
            context=state.ctx,
            attributes={
                "session.id": session_id,
                "langfuse.session.id": session_id,
            },
        )
    except Exception as exc:
        logger.warning("tracing: failed to start turn span: %s", exc)


def record_model_request_start(
    session_id: str, span_id: str, provider_name: str, model_id: str
) -> None:
    """Mirror span.model_request_start — open a model_request span under the turn."""
    if _tracer is None:
        return
    try:
        state = _session_state(session_id)
        parent_ctx = state.ctx
        if state.turn_span is not None:
            parent_ctx = trace.set_span_in_context(state.turn_span, state.ctx)
        state.model_spans[span_id] = _tracer.start_span(
            "model_request",
            context=parent_ctx,
            attributes={
                "session.id": session_id,
                "langfuse.session.id": session_id,
                "gen_ai.system": provider_name,
                "gen_ai.request.model": model_id,
                "model_request.span_id": span_id,
            },
        )
    except Exception as exc:
        logger.warning("tracing: failed to start model request span: %s", exc)


def record_model_request_end(
    session_id: str,
    span_id: str,
    *,
    usage: dict | None = None,
    elapsed_ms: int | None = None,
    error: str | None = None,
) -> None:
    """Mirror span.model_request_end — close the paired model_request span."""
    if _tracer is None:
        return
    try:
        state = _sessions.get(session_id)
        if state is None:
            return
        span = state.model_spans.pop(span_id, None)
        if span is None:
            return
        attrs: dict = {}
        if usage:
            for key, attr in (
                ("input_tokens", "gen_ai.usage.input_tokens"),
                ("output_tokens", "gen_ai.usage.output_tokens"),
                ("cache_creation_input_tokens", "gen_ai.usage.cache_creation_input_tokens"),
                ("cache_read_input_tokens", "gen_ai.usage.cache_read_input_tokens"),
            ):
                value = usage.get(key)
                if value is not None:
                    attrs[attr] = value
        if elapsed_ms is not None:
            attrs["elapsed_ms"] = elapsed_ms
        if error:
            attrs["error"] = error[:512]
            span.set_status(Status(StatusCode.ERROR, error[:512]))
        if attrs:
            span.set_attributes(attrs)
        span.end()
    except Exception as exc:
        logger.warning("tracing: failed to end model request span: %s", exc)


def end_session_tracing(session_id: str) -> None:
    """Close any open turn/model spans and drop the session's tracing state."""
    if _tracer is None:
        return
    try:
        state = _sessions.pop(session_id, None)
        if state is None:
            return
        if state.turn_span is not None:
            state.turn_span.end()
            state.turn_span = None
        for span in state.model_spans.values():
            span.end()
        state.model_spans.clear()
    except Exception as exc:
        logger.warning("tracing: failed to end session tracing: %s", exc)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def setup_tracing() -> bool:
    """Initialize OTel tracing from env config. Returns True when enabled."""
    global _provider, _tracer
    if not _env_flag("LINCHPIN_OTEL_ENABLED"):
        return False
    if _provider is not None:
        return True
    try:
        # SDK / exporter are lazy imports: only needed when tracing is on.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = os.environ.get("LINCHPIN_OTLP_ENDPOINT", "").strip() or _DEFAULT_OTLP_ENDPOINT
        headers = _parse_headers(os.environ.get("LINCHPIN_OTLP_HEADERS", ""))
        timeout_raw = os.environ.get("LINCHPIN_OTLP_TIMEOUT_SEC", "").strip()
        timeout = float(timeout_raw) if timeout_raw else _DEFAULT_OTLP_TIMEOUT
        service_name = os.environ.get("LINCHPIN_OTEL_SERVICE_NAME", "").strip() or _DEFAULT_SERVICE_NAME

        exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=timeout)
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        _activate(provider)
        logger.info(
            "OpenTelemetry tracing enabled (endpoint=%s, service=%s).", endpoint, service_name
        )
        return True
    except Exception as exc:
        logger.warning("tracing: setup failed, continuing without OTel export: %s", exc)
        return False


def _activate(provider: "TracerProvider") -> None:
    """Install a provider without touching the global OTel state (test hook)."""
    global _provider, _tracer
    _provider = provider
    _tracer = provider.get_tracer("linchpin-api")


def shutdown_tracing() -> None:
    """Flush and release OTel resources. Safe to call repeatedly / when disabled."""
    global _provider, _tracer
    provider, _provider = _provider, None
    _tracer = None
    _sessions.clear()
    if provider is not None:
        try:
            provider.shutdown()
        except Exception as exc:
            logger.warning("tracing: provider shutdown failed: %s", exc)


# Test hook: drop module state and shut the provider down (flushes spans).
_deactivate = shutdown_tracing
