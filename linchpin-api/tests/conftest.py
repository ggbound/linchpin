"""Shared test fixtures for linchpin-api tests."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.providers import ContentBlock, ModelResponse, StreamChunk


def model_response_to_stream_chunks(response: ModelResponse):
    """Convert a ModelResponse into a list of StreamChunks for mocking send_streaming.

    This allows existing tests that create ModelResponse objects to also work
    with the streaming orchestrator path.
    """
    chunks: list[StreamChunk] = []
    for block in response.content:
        if block.type == "text":
            chunks.append(StreamChunk(type="text_delta", text=block.text))
        elif block.type == "thinking":
            chunks.append(StreamChunk(type="thinking", text=block.text))
        elif block.type == "tool_use":
            chunks.append(StreamChunk(
                type="tool_use",
                tool_use_id=block.tool_use_id,
                tool_name=block.tool_name,
                tool_input=block.tool_input,
            ))
    chunks.append(StreamChunk(
        type="final",
        stop_reason=response.stop_reason,
        usage=response.usage,
    ))
    return chunks


def make_streaming_mock(response: ModelResponse):
    """Create an async generator function that yields StreamChunks from a ModelResponse."""
    chunks = model_response_to_stream_chunks(response)

    async def _stream(*args, **kwargs):
        for chunk in chunks:
            yield chunk

    return _stream


def setup_mock_provider(mock_provider, response: ModelResponse | None = None, error: Exception | None = None):
    """Configure a mock provider with both send() and send_streaming() methods.

    If error is provided, both methods will raise it.
    If response is provided, send() returns it and send_streaming() yields equivalent chunks.
    """
    if error is not None:
        mock_provider.send = AsyncMock(side_effect=error)

        async def _error_stream(*args, **kwargs):
            raise error
            yield  # make it an async generator  # noqa: E501

        mock_provider.send_streaming = _error_stream
    elif response is not None:
        mock_provider.send = AsyncMock(return_value=response)
        mock_provider.send_streaming = make_streaming_mock(response)


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch: pytest.MonkeyPatch):
    """Ensure LINCHPIN_API_KEY is set for every test."""
    monkeypatch.setenv("LINCHPIN_API_KEY", "test-secret-key")
    # v0.2.0 item #14 — skip the webhook delivery worker in tests so it
    # doesn't try to POST anywhere. Individual tests that want to
    # exercise the worker monkeypatch this back on.
    monkeypatch.setenv("LINCHPIN_WEBHOOKS_WORKER", "false")
    # v0.3 PR5 — skip the memory GC cron in tests so the loop doesn't
    # tick during a `with TestClient(app)` lifespan. Tests that
    # exercise GC directly call ``run_memory_gc()``.
    monkeypatch.setenv("LINCHPIN_MEMORY_GC", "false")

    # Atlas B10 — keep the metrics gauges sampler out of tests (it would
    # tick against an uninitialised DB pool). /metrics itself is still
    # exercised in test_metrics.py.
    monkeypatch.setenv("LINCHPIN_METRICS_SAMPLER", "false")

    # Atlas B7 — keep the deployment scheduler out of tests (same reason:
    # it would poll an uninitialised DB pool). Individual tests that
    # exercise the loop monkeypatch this back on.
    monkeypatch.setenv("LINCHPIN_DEPLOYMENTS_SCHEDULER", "false")

    # Atlas C2 — keep the lease supervisor out of tests (it would poll the
    # uninitialised DB pool). Takeover is exercised directly in test_cluster.py.
    monkeypatch.setenv("LINCHPIN_LEASE_SUPERVISOR", "false")


@pytest.fixture(autouse=True)
def _isolated_event_crypto(monkeypatch: pytest.MonkeyPatch):
    """Atlas B8 — isolate event-encryption state for unit tests.

    - Starts every test with an empty in-process data-key cache so a key
      cached by one test can't leak into another.
    - Defaults the crypto module's DB lookup to "no key" (None): the
      asyncpg pool is never initialised in unit tests, so a live
      ``fetch_one`` would raise RuntimeError. Tests that exercise the
      DB fallback patch ``app.event_crypto.fetch_one`` themselves.
    - Atlas M6 — same isolation for the BYOK KEK cache.
    """
    from app import event_crypto

    event_crypto._data_key_cache.clear()
    event_crypto._kek_cache.clear()
    monkeypatch.setattr(
        event_crypto, "fetch_one", AsyncMock(return_value=None)
    )
    yield
    event_crypto._data_key_cache.clear()
    event_crypto._kek_cache.clear()


@pytest.fixture()
def client():
    """Return a TestClient that skips DB/migration/Docker startup."""
    # Patch out lifespan dependencies so we can test HTTP layer in isolation
    with (
        patch("app.main.check_migrations_current"),
        patch("app.main.create_pool", new_callable=AsyncMock),
        patch("app.main.close_pool", new_callable=AsyncMock),
        patch("app.main.ensure_docker_networks", new_callable=AsyncMock),
        patch("app.main.DockerSandbox") as MockSandbox,
        patch("app.main.cleanup_expired_sessions", new_callable=AsyncMock),
        patch("app.main.recover_sessions", new_callable=AsyncMock),
        # Atlas A8 — keep the idempotency-key cleanup loop out of test logs.
        patch("app.main.idempotency_cleanup_loop", new_callable=AsyncMock),
        # Atlas B12 — keep the events retention loop out of test logs.
        patch("app.main.events_retention_cleanup_loop", new_callable=AsyncMock),
    ):
        mock_sandbox = MagicMock()
        mock_sandbox.create = AsyncMock(return_value="container-mock")
        mock_sandbox.destroy = AsyncMock()
        # v0.2.0 item #3 — sessions now route image selection through
        # sandbox.ensure_image(). Default behavior in tests is pass-through:
        # whatever base_image the route resolved is what create() sees.
        mock_sandbox.ensure_image = AsyncMock(
            side_effect=lambda *, base_image, packages: base_image
        )
        MockSandbox.return_value = mock_sandbox

        from app.main import app

        with TestClient(app) as c:
            yield c
