"""Test: Orchestrator credential resolution for the direct OpenAI provider.

Covers the three resolution paths added for provider="openai":
1. No vaults bound — OPENAI_API_KEY env fallback is passed to the provider.
2. Vaults bound — CredentialResolver is queried with provider="openai".
3. No credential anywhere — session fails with credential_missing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Agent, ModelConfig
from app.orchestrator import run_session
from app.providers import ContentBlock, ModelResponse
from tests.conftest import model_response_to_stream_chunks


_SESSION_ID = str(uuid.uuid4())
_AGENT_ID = str(uuid.uuid4())
_ENV_ID = str(uuid.uuid4())
_VAULT_ID = str(uuid.uuid4())
_CONTAINER_ID = "container-test"


def _make_agent() -> Agent:
    return Agent(
        id=_AGENT_ID,
        name="test-openai-agent",
        version=1,
        model=ModelConfig(provider="openai", id="gpt-4o"),
        system="You are a helpful assistant.",
        tools=[],
        mcp_servers=[],
        created_at=datetime.now(timezone.utc),
    )


def _make_session_row(status="idle"):
    return {
        "id": uuid.UUID(_SESSION_ID),
        "agent_id": uuid.UUID(_AGENT_ID),
        "agent_version": None,
        "environment_id": uuid.UUID(_ENV_ID),
        "status": status,
        "container_id": _CONTAINER_ID,
        "title": "Test",
        "metadata": {},
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "archived_at": None,
        "last_event_cursor": None,
        "ttl_seconds": None,
        "stats": {"total_events": 0, "tool_calls": 0, "model_turns": 0},
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "vault_ids": [],
    }


def _listen_once():
    """Return an async generator that yields one notification then stops."""
    async def _gen(channel):
        yield "new_events"
    return _gen


def _text_response() -> ModelResponse:
    return ModelResponse(
        content=[ContentBlock(type="text", text="Hello from OpenAI!")],
        stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def _make_stream_recorder(mock_provider, response: ModelResponse) -> dict:
    """Attach a send_streaming mock that records its kwargs and yields chunks."""
    stream_kwargs: dict = {}

    async def _stream(*args, **kwargs):
        stream_kwargs.update(kwargs)
        for chunk in model_response_to_stream_chunks(response):
            yield chunk

    mock_provider.send_streaming = _stream
    return stream_kwargs


class TestOrchestratorOpenAI:
    @pytest.mark.asyncio
    async def test_env_key_used_when_no_vault(self, monkeypatch):
        """Without vaults, OPENAI_API_KEY from the environment is passed to the provider."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")

        agent = _make_agent()
        session_row = _make_session_row(status="idle")
        session_row["vault_ids"] = []

        mock_provider = AsyncMock()
        stream_kwargs = _make_stream_recorder(mock_provider, _text_response())

        appended_events = []

        async def mock_append(sid, etype, payload):
            appended_events.append((etype, payload))
            return MagicMock(session_id=sid, cursor="c1", seq=len(appended_events), type=etype, payload=payload, processed_at=None)

        call_count = 0

        async def mock_load_session(sid):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return session_row
            return {**session_row, "status": "terminated"}

        sandbox = MagicMock()

        with (
            patch("app.orchestrator.load_session", side_effect=mock_load_session),
            patch("app.orchestrator.load_agent", new_callable=AsyncMock, return_value=agent),
            patch("app.orchestrator.listen", side_effect=_listen_once()),
            patch("app.orchestrator.append_event", side_effect=mock_append),
            patch("app.orchestrator.transition", new_callable=AsyncMock),
            patch("app.orchestrator.update_usage", new_callable=AsyncMock),
            patch("app.orchestrator.get_provider", return_value=mock_provider),
            patch("app.orchestrator.build_tool_definitions", return_value=[]),
            patch("app.orchestrator.check_for_interrupt", new_callable=AsyncMock, return_value=False),
            patch("app.orchestrator.build_context", new_callable=AsyncMock, return_value=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hey"},
            ]),
            patch("app.orchestrator.CredentialResolver") as MockResolver,
        ):
            await run_session(_SESSION_ID, sandbox)

        # No vaults bound — the resolver must not be consulted.
        MockResolver.assert_not_called()

        # Env fallback key must ride on the provider call.
        assert stream_kwargs.get("api_key") == "sk-env-openai"

        event_types = [e[0] for e in appended_events]
        assert "agent.message" in event_types

    @pytest.mark.asyncio
    async def test_vault_resolves_openai_credential(self, monkeypatch):
        """With vaults bound, the resolver is queried with provider="openai"."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        agent = _make_agent()
        session_row = _make_session_row(status="idle")
        session_row["vault_ids"] = [_VAULT_ID]

        mock_provider = AsyncMock()
        stream_kwargs = _make_stream_recorder(mock_provider, _text_response())

        appended_events = []

        async def mock_append(sid, etype, payload):
            appended_events.append((etype, payload))
            return MagicMock(session_id=sid, cursor="c1", seq=len(appended_events), type=etype, payload=payload, processed_at=None)

        call_count = 0

        async def mock_load_session(sid):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return session_row
            return {**session_row, "status": "terminated"}

        mock_resolve_api_key = AsyncMock(return_value="sk-vault-openai")

        sandbox = MagicMock()

        with (
            patch("app.orchestrator.load_session", side_effect=mock_load_session),
            patch("app.orchestrator.load_agent", new_callable=AsyncMock, return_value=agent),
            patch("app.orchestrator.listen", side_effect=_listen_once()),
            patch("app.orchestrator.append_event", side_effect=mock_append),
            patch("app.orchestrator.transition", new_callable=AsyncMock),
            patch("app.orchestrator.update_usage", new_callable=AsyncMock),
            patch("app.orchestrator.get_provider", return_value=mock_provider),
            patch("app.orchestrator.build_tool_definitions", return_value=[]),
            patch("app.orchestrator.check_for_interrupt", new_callable=AsyncMock, return_value=False),
            patch("app.orchestrator.build_context", new_callable=AsyncMock, return_value=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hey"},
            ]),
            patch("app.orchestrator.CredentialResolver") as MockResolver,
        ):
            mock_resolver_instance = MagicMock()
            mock_resolver_instance.resolve_api_key = mock_resolve_api_key
            MockResolver.return_value = mock_resolver_instance

            await run_session(_SESSION_ID, sandbox)

        # Resolver must be queried with the provider string, not the endpoint.
        mock_resolve_api_key.assert_called_once_with([_VAULT_ID], "openai")

        # Vault credential wins over any env fallback.
        assert stream_kwargs.get("api_key") == "sk-vault-openai"

        event_types = [e[0] for e in appended_events]
        assert "agent.message" in event_types

    @pytest.mark.asyncio
    async def test_missing_key_fails_with_credential_missing(self, monkeypatch):
        """Vaults bound but no credential found and no env key — the session
        fails with credential_missing (openai, unlike ollama, needs a key)."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        agent = _make_agent()
        session_row = _make_session_row(status="idle")
        session_row["vault_ids"] = [_VAULT_ID]

        mock_provider = AsyncMock()
        stream_kwargs = _make_stream_recorder(mock_provider, _text_response())

        appended_events = []
        transitions = []

        async def mock_append(sid, etype, payload):
            appended_events.append((etype, payload))
            return MagicMock(session_id=sid, cursor="c1", seq=len(appended_events), type=etype, payload=payload, processed_at=None)

        async def mock_transition(sid, status, stop_reason=None):
            transitions.append((status, stop_reason))

        call_count = 0

        async def mock_load_session(sid):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return session_row
            return {**session_row, "status": "terminated"}

        mock_resolve_api_key = AsyncMock(return_value=None)

        sandbox = MagicMock()

        with (
            patch("app.orchestrator.load_session", side_effect=mock_load_session),
            patch("app.orchestrator.load_agent", new_callable=AsyncMock, return_value=agent),
            patch("app.orchestrator.listen", side_effect=_listen_once()),
            patch("app.orchestrator.append_event", side_effect=mock_append),
            patch("app.orchestrator.transition", side_effect=mock_transition),
            patch("app.orchestrator.update_usage", new_callable=AsyncMock),
            patch("app.orchestrator.get_provider", return_value=mock_provider),
            patch("app.orchestrator.build_tool_definitions", return_value=[]),
            patch("app.orchestrator.check_for_interrupt", new_callable=AsyncMock, return_value=False),
            patch("app.orchestrator.build_context", new_callable=AsyncMock, return_value=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hey"},
            ]),
            patch("app.orchestrator.CredentialResolver") as MockResolver,
        ):
            mock_resolver_instance = MagicMock()
            mock_resolver_instance.resolve_api_key = mock_resolve_api_key
            MockResolver.return_value = mock_resolver_instance

            await run_session(_SESSION_ID, sandbox)

        # The resolver was consulted but found nothing.
        mock_resolve_api_key.assert_called_once_with([_VAULT_ID], "openai")

        # The provider must never be invoked.
        assert stream_kwargs == {}

        # A session.error naming the env var must be emitted...
        error_events = [e for e in appended_events if e[0] == "session.error"]
        assert len(error_events) == 1
        assert error_events[0][1]["source"] == "credential_resolution"
        assert "OPENAI_API_KEY" in error_events[0][1]["error"]

        # ...and the session must transition to failed with credential_missing.
        assert ("failed", "credential_missing") in transitions
