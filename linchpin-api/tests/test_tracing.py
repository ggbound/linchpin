"""Tests for OpenTelemetry tracing hooks (Atlas B10).

Uses InMemorySpanExporter + SimpleSpanProcessor injected via the module-level
_activate() hook — no network I/O, no global OTel provider state.
"""

import uuid

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app import tracing


@pytest.fixture()
def otel():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing._activate(provider)
    yield exporter
    tracing._deactivate()


def _finished(exporter):
    return list(exporter.get_finished_spans())


# ---------------------------------------------------------------------------
# Disabled / lifecycle behavior
# ---------------------------------------------------------------------------


def test_hooks_noop_when_disabled():
    """Without an activated provider all hooks are zero-cost no-ops."""
    assert tracing._tracer is None
    sid = str(uuid.uuid4())
    tracing.start_turn(sid)
    tracing.record_model_request_start(sid, "sp", "anthropic", "claude-x")
    tracing.record_model_request_end(sid, "sp", usage={"input_tokens": 1}, elapsed_ms=5, error=None)
    tracing.end_session_tracing(sid)
    assert tracing._sessions == {}


def test_setup_tracing_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LINCHPIN_OTEL_ENABLED", raising=False)
    assert tracing.setup_tracing() is False
    assert tracing._tracer is None
    assert tracing._provider is None


def test_setup_tracing_enabled_builds_provider(monkeypatch):
    monkeypatch.setenv("LINCHPIN_OTEL_ENABLED", "1")
    monkeypatch.delenv("LINCHPIN_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("LINCHPIN_OTLP_HEADERS", raising=False)
    try:
        assert tracing.setup_tracing() is True
        assert tracing._tracer is not None
        assert tracing._provider is not None
    finally:
        tracing.shutdown_tracing()  # stop the BatchSpanProcessor worker
    assert tracing._tracer is None
    assert tracing._sessions == {}


def test_parse_headers():
    assert tracing._parse_headers("Authorization=Basic dXNlcjpwYXNz,X-Env=dev") == {
        "Authorization": "Basic dXNlcjpwYXNz",
        "X-Env": "dev",
    }
    assert tracing._parse_headers("") == {}
    assert tracing._parse_headers("novalue") == {}
    # base64 padding survives: split on the first '=' only
    assert tracing._parse_headers("Authorization=Basic abc==")["Authorization"] == "Basic abc=="


# ---------------------------------------------------------------------------
# Span structure
# ---------------------------------------------------------------------------


def test_model_span_attributes_and_parenting(otel):
    sid = str(uuid.uuid4())
    tracing.start_turn(sid)
    tracing.record_model_request_start(sid, "sp1", "anthropic", "claude-sonnet")
    tracing.record_model_request_end(
        sid, "sp1",
        usage={
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 3,
        },
        elapsed_ms=42,
    )
    tracing.end_session_tracing(sid)

    spans = {s.name: s for s in _finished(otel)}
    assert set(spans) == {"agent.turn", "model_request"}
    model, turn = spans["model_request"], spans["agent.turn"]

    assert model.attributes["gen_ai.system"] == "anthropic"
    assert model.attributes["gen_ai.request.model"] == "claude-sonnet"
    assert model.attributes["gen_ai.usage.input_tokens"] == 10
    assert model.attributes["gen_ai.usage.output_tokens"] == 5
    assert model.attributes["gen_ai.usage.cache_creation_input_tokens"] == 2
    assert model.attributes["gen_ai.usage.cache_read_input_tokens"] == 3
    assert model.attributes["elapsed_ms"] == 42
    assert model.attributes["session.id"] == sid
    assert model.attributes["langfuse.session.id"] == sid

    # model_request is a child of the agent.turn span, same trace
    assert model.parent is not None
    assert model.parent.span_id == turn.context.span_id
    assert model.context.trace_id == turn.context.trace_id


def test_same_session_shares_trace_across_turns(otel):
    sid = str(uuid.uuid4())
    tracing.start_turn(sid)
    tracing.record_model_request_start(sid, "a", "p", "m")
    tracing.record_model_request_end(sid, "a", elapsed_ms=1)
    tracing.end_session_tracing(sid)  # closes turn 1
    tracing.start_turn(sid)  # turn 2 — fresh state, same trace
    tracing.end_session_tracing(sid)

    spans = _finished(otel)
    assert len(spans) == 3  # turn1 + model + turn2
    assert len({s.context.trace_id for s in spans}) == 1
    assert {s.context.trace_id for s in spans} == {uuid.UUID(sid).int}
    assert [s.name for s in spans].count("agent.turn") == 2


def test_different_sessions_different_traces(otel):
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    tracing.start_turn(s1)
    tracing.start_turn(s2)
    tracing.end_session_tracing(s1)
    tracing.end_session_tracing(s2)

    spans = _finished(otel)
    assert {s.context.trace_id for s in spans} == {uuid.UUID(s1).int, uuid.UUID(s2).int}


def test_error_sets_error_status_and_truncates(otel):
    sid = str(uuid.uuid4())
    tracing.start_turn(sid)
    tracing.record_model_request_start(sid, "sp", "p", "m")
    tracing.record_model_request_end(sid, "sp", error="x" * 600)

    model = next(s for s in _finished(otel) if s.name == "model_request")
    assert model.status.status_code == StatusCode.ERROR
    assert model.status.description == "x" * 512
    assert model.attributes["error"] == "x" * 512


def test_end_session_tracing_flushes_unclosed_spans(otel):
    """A crashed model request still exports its spans via session teardown."""
    sid = str(uuid.uuid4())
    tracing.start_turn(sid)
    tracing.record_model_request_start(sid, "leak", "p", "m")
    tracing.end_session_tracing(sid)  # no record_model_request_end

    assert len(_finished(otel)) == 2  # model + turn
    assert tracing._sessions == {}


def test_unpaired_end_ignored(otel):
    sid = str(uuid.uuid4())
    tracing.start_turn(sid)
    tracing.record_model_request_end(sid, "ghost")  # never started
    tracing.end_session_tracing(sid)
    assert [s.name for s in _finished(otel)] == ["agent.turn"]


def test_model_span_without_turn_still_recorded(otel):
    """Hooks tolerate a model request before any start_turn (lazy state)."""
    sid = str(uuid.uuid4())
    tracing.record_model_request_start(sid, "sp", "ollama", "llama3")
    tracing.record_model_request_end(sid, "sp", elapsed_ms=7)

    spans = _finished(otel)
    assert len(spans) == 1
    assert spans[0].name == "model_request"
    assert spans[0].attributes["gen_ai.system"] == "ollama"
    assert spans[0].context.trace_id == uuid.UUID(sid).int
