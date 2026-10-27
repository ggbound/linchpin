"""Model provider adapters.

Linchpin supports two providers:

- ``openrouter`` — cloud aggregator that exposes ~200 models (Claude, GPT,
  Gemini, Llama, DeepSeek, Mistral, Qwen, …) behind a single
  OpenAI-compatible HTTP API.
- ``ollama`` — local inference via the Ollama REST API.

Each adapter constructs provider-specific requests, parses responses into
a unified ``ModelResponse``, and retries retryable errors with exponential
backoff (3 attempts).
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.models import ModelConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unified response types
# ---------------------------------------------------------------------------


@dataclass
class ContentBlock:
    """A single block in a model response."""

    type: str  # "text", "tool_use", "thinking"
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict | None = None


@dataclass
class ModelResponse:
    """Unified response from any model provider."""

    content: list[ContentBlock] = field(default_factory=list)
    stop_reason: str | None = None
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})


@dataclass
class StreamChunk:
    """A single chunk from a streaming model response."""

    type: str  # "text_delta", "tool_use", "thinking", "final"
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict | None = None
    stop_reason: str | None = None
    usage: dict | None = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Raised when a provider request fails after all retries."""


class RetryableError(Exception):
    """Raised for errors that should be retried."""


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


async def _retry_with_backoff(coro_factory, max_retries: int = 3, base_delay: float = 1.0):
    """Run ``coro_factory()`` with exponential-backoff retry on RetryableError."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except RetryableError as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Retryable error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, max_retries, delay, exc,
                )
                await asyncio.sleep(delay)
    raise ProviderError(
        f"Provider request failed after {max_retries} attempts: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class ModelProviderProtocol(Protocol):
    """Interface that all model provider adapters must satisfy."""

    async def send(
        self,
        messages: list[dict],
        config: ModelConfig,
        tools: list[dict] | None = None,
        api_key: str | None = None,
    ) -> ModelResponse: ...

    async def send_streaming(
        self,
        messages: list[dict],
        config: ModelConfig,
        tools: list[dict] | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[StreamChunk]: ...


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def _extract_openrouter_error(resp: httpx.Response) -> str:
    """Return a human-readable error string from an OpenRouter error response.

    OpenRouter returns ``{"error": {"message": "...", "code": ...}}`` on
    failure. Falls back to the raw body if the JSON shape is unexpected.
    """
    try:
        data = resp.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        return resp.text or resp.reason_phrase
    except Exception:
        return resp.text or resp.reason_phrase


async def _read_stream_body(resp: httpx.Response) -> str:
    """Read the body of a streaming response that hasn't been consumed yet."""
    try:
        await resp.aread()
    except Exception:
        return resp.reason_phrase
    return _extract_openrouter_error(resp)


def _block_to_openai(block: dict) -> dict | None:
    """Translate a Linchpin content block to its OpenAI Chat Completions
    counterpart. Returns ``None`` for blocks we don't know how to map —
    the caller drops them so the provider sees a clean ``content[]``.

    OpenRouter routes Linchpin's three image source shapes (base64,
    url, file) into the OpenAI ``image_url`` block. ``file`` source is
    resolved up the stack before this helper sees it; if it's still
    present here, we keep it as-is so the upstream test mocks can
    assert the raw shape.
    """
    btype = block.get("type")
    if btype == "text":
        return {"type": "text", "text": block.get("text", "")}
    if btype == "image":
        src = block.get("source") or {}
        if src.get("type") == "url":
            return {"type": "image_url", "image_url": {"url": src.get("url", "")}}
        if src.get("type") == "base64":
            media_type = src.get("media_type", "image/png")
            data = src.get("data", "")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"},
            }
        # file source — propagate verbatim; the orchestrator should have
        # resolved it to inline bytes before send. Surface as-is so a
        # test can spot the un-resolved leak.
        return {"type": "image_url", "image_url": src}
    if btype == "document":
        # OpenRouter doesn't have a portable document block shape; pass
        # the Linchpin block through verbatim so Claude-routed requests
        # work (Anthropic accepts ``{type: "document", ...}``) and
        # non-Anthropic models surface a clean upstream error.
        return block
    # Unknown block kind — drop. Pydantic's discriminator should have
    # rejected this at the API boundary, so reaching here is a bug.
    return None


def _convert_messages_for_openai(messages: list[dict]) -> list[dict]:
    """Walk the message list and translate Anthropic-style structured
    content into the OpenAI Chat Completions shape. String content is
    untouched. Returns a new list — the caller's messages are not
    mutated.

    Tool-call history mapping (required — dropping these blocks makes
    the model re-issue the same tool call every turn because it never
    sees its prior calls or their results):
    - assistant ``tool_use`` blocks → assistant ``tool_calls`` entries
    - user ``tool_result`` blocks   → ``role: "tool"`` messages
    - text/image blocks in list content → OpenAI content blocks

    Consecutive assistant messages (text + tool call) are merged into a
    single assistant turn so providers that require strict turn
    alternation stay happy.
    """
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue

        text_blocks: list[dict] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                tool_input = block.get("input")
                if tool_input is None:
                    tool_input = block.get("tool_input") or {}
                tool_calls.append({
                    "id": block.get("id") or block.get("tool_use_id") or "",
                    "type": "function",
                    "function": {
                        "name": block.get("name") or block.get("tool_name") or "",
                        "arguments": _json.dumps(tool_input, ensure_ascii=False),
                    },
                })
            elif btype == "tool_result":
                tool_results.append(block)
            else:
                translated = _block_to_openai(block)
                if translated is not None:
                    text_blocks.append(translated)

        if role == "assistant":
            converted: dict = {"role": "assistant", "content": text_blocks or None}
            if tool_calls:
                converted["tool_calls"] = tool_calls
            if out and out[-1].get("role") == "assistant":
                prev = out[-1]
                prev_content = prev.get("content")
                prev_blocks = (
                    [{"type": "text", "text": prev_content}]
                    if isinstance(prev_content, str) and prev_content
                    else list(prev_content or [])
                )
                merged_blocks = prev_blocks + (converted.get("content") or [])
                prev["content"] = merged_blocks or None
                prev["tool_calls"] = (prev.get("tool_calls") or []) + tool_calls
            else:
                out.append(converted)
            continue

        # Non-assistant roles: tool_result blocks become role:"tool"
        # messages; any remaining text/image blocks stay with the
        # original role.
        for block in tool_results:
            result_content = block.get("content")
            if not isinstance(result_content, str):
                result_content = (
                    _json.dumps(result_content, ensure_ascii=False)
                    if result_content is not None
                    else ""
                )
            out.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id") or block.get("tool_call_id") or "",
                "content": result_content,
            })
        if text_blocks:
            out.append({**msg, "content": text_blocks})

    return out


def _convert_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Linchpin tool definitions to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        }
        for t in tools
    ]


def _parse_openrouter_response(data: dict) -> ModelResponse:
    """Convert an OpenRouter /chat/completions JSON response to a ModelResponse.

    OpenRouter responses follow the OpenAI Chat Completions shape.
    """
    choices = data.get("choices") or []
    if not choices:
        return ModelResponse()

    choice = choices[0]
    msg = choice.get("message", {}) or {}
    blocks: list[ContentBlock] = []

    if msg.get("content"):
        blocks.append(ContentBlock(type="text", text=msg["content"]))

    for tc in msg.get("tool_calls") or []:
        func = tc.get("function", {}) or {}
        raw_args = func.get("arguments", "")
        if isinstance(raw_args, str):
            try:
                tool_input = _json.loads(raw_args) if raw_args else {}
            except _json.JSONDecodeError:
                tool_input = {"raw": raw_args}
        else:
            tool_input = raw_args or {}
        blocks.append(ContentBlock(
            type="tool_use",
            tool_use_id=tc.get("id"),
            tool_name=func.get("name"),
            tool_input=tool_input,
        ))

    finish = choice.get("finish_reason")
    stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"

    usage_raw = data.get("usage") or {}
    # v0.2.0 item #10 — surface prompt-caching counters from the OpenRouter /
    # OpenAI-style response when present. Anthropic exposes cache_creation /
    # cache_read on `prompt_tokens_details.cached_tokens`; OpenRouter mirrors
    # this for upstream providers that report it. Missing fields → 0.
    prompt_details = usage_raw.get("prompt_tokens_details") or {}
    cache_read = prompt_details.get("cached_tokens", 0) or 0
    # Anthropic-via-OpenRouter exposes cache *creation* under cache_creation_input_tokens
    # at the top level; OpenAI doesn't have a cache-creation concept.
    cache_creation = usage_raw.get("cache_creation_input_tokens", 0) or 0
    usage = {
        "input_tokens": usage_raw.get("prompt_tokens", 0) or 0,
        "output_tokens": usage_raw.get("completion_tokens", 0) or 0,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
    }

    return ModelResponse(content=blocks, stop_reason=stop_reason, usage=usage)


class OpenRouterProvider:
    """Adapter for the OpenRouter chat-completions API.

    OpenRouter speaks the OpenAI Chat Completions protocol, so requests and
    responses use the standard OAI shape. The provider sends raw HTTP via
    ``httpx`` rather than depending on the ``openai`` SDK.
    """

    # Upstream label used in error messages; ``OpenAIProvider`` overrides it
    # so failures surface the right vendor name.
    _label = "OpenRouter"

    def __init__(self, base_url: str = OPENROUTER_DEFAULT_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)

    def _build_headers(self, api_key: str | None) -> dict:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = "https://github.com/flowagent-sh/linchpin"
        headers["X-Title"] = "Linchpin"
        return headers

    def _build_body(
        self,
        messages: list[dict],
        config: ModelConfig,
        tools: list[dict] | None,
        stream: bool,
    ) -> dict:
        body: dict = {
            "model": config.id,
            "messages": _convert_messages_for_openai(messages),
        }
        if tools:
            body["tools"] = _convert_tools_to_openai(tools)
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return body

    async def send(
        self,
        messages: list[dict],
        config: ModelConfig,
        tools: list[dict] | None = None,
        api_key: str | None = None,
    ) -> ModelResponse:
        body = self._build_body(messages, config, tools, stream=False)
        headers = self._build_headers(api_key)

        async def _call():
            try:
                resp = await self._client.post(
                    f"{self._base_url}/chat/completions", json=body, headers=headers,
                )
                if resp.status_code in (429, 500, 502, 503):
                    raise RetryableError(
                        f"{self._label} returned {resp.status_code}: "
                        f"{_extract_openrouter_error(resp)}"
                    )
                if resp.status_code >= 400:
                    raise ProviderError(
                        f"{self._label} returned {resp.status_code}: "
                        f"{_extract_openrouter_error(resp)}"
                    )
                return _parse_openrouter_response(resp.json())
            except (RetryableError, ProviderError):
                raise
            except httpx.ConnectError as exc:
                raise RetryableError(str(exc)) from exc
            except Exception as exc:
                raise ProviderError(str(exc)) from exc

        return await _retry_with_backoff(_call)

    async def send_streaming(
        self,
        messages: list[dict],
        config: ModelConfig,
        tools: list[dict] | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        body = self._build_body(messages, config, tools, stream=True)
        headers = self._build_headers(api_key)

        async def _open_stream():
            try:
                req = self._client.build_request(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=body,
                    headers=headers,
                )
                resp = await self._client.send(req, stream=True)
                if resp.status_code in (429, 500, 502, 503):
                    detail = await _read_stream_body(resp)
                    await resp.aclose()
                    raise RetryableError(
                        f"{self._label} returned {resp.status_code}: {detail}"
                    )
                if resp.status_code >= 400:
                    detail = await _read_stream_body(resp)
                    await resp.aclose()
                    raise ProviderError(
                        f"{self._label} returned {resp.status_code}: {detail}"
                    )
                return resp
            except (RetryableError, ProviderError):
                raise
            except httpx.ConnectError as exc:
                raise RetryableError(str(exc)) from exc
            except Exception as exc:
                raise ProviderError(str(exc)) from exc

        resp = await _retry_with_backoff(_open_stream)

        tool_calls: dict[int, dict] = {}
        usage: dict | None = None
        stop_reason = "end_turn"

        try:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    chunk = _json.loads(payload)
                except _json.JSONDecodeError:
                    continue

                if chunk.get("usage"):
                    u = chunk["usage"]
                    # v0.2.0 item #10 — see provider.send() above for mapping.
                    prompt_details = u.get("prompt_tokens_details") or {}
                    usage = {
                        "input_tokens": u.get("prompt_tokens", 0) or 0,
                        "output_tokens": u.get("completion_tokens", 0) or 0,
                        "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0) or 0,
                        "cache_read_input_tokens": prompt_details.get("cached_tokens", 0) or 0,
                    }

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}

                if delta.get("content"):
                    yield StreamChunk(type="text_delta", text=delta["content"])

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_calls.setdefault(
                        idx, {"id": "", "name": "", "args": ""}
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    func = tc.get("function") or {}
                    if func.get("name"):
                        slot["name"] = func["name"]
                    if func.get("arguments"):
                        slot["args"] += func["arguments"]

                finish = choice.get("finish_reason")
                if finish:
                    if finish == "tool_calls":
                        stop_reason = "tool_use"
                    elif finish == "stop":
                        stop_reason = "end_turn"
                    else:
                        stop_reason = finish

                    for slot in tool_calls.values():
                        try:
                            tool_input = _json.loads(slot["args"]) if slot["args"] else {}
                        except _json.JSONDecodeError:
                            tool_input = {"raw": slot["args"]}
                        yield StreamChunk(
                            type="tool_use",
                            tool_use_id=slot["id"],
                            tool_name=slot["name"],
                            tool_input=tool_input,
                        )
                    tool_calls.clear()
                    yield StreamChunk(
                        type="final",
                        stop_reason=stop_reason,
                        usage=usage or {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                    )
        finally:
            await resp.aclose()


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider(OpenRouterProvider):
    """Adapter for the OpenAI chat-completions API.

    OpenAI uses the same wire protocol as OpenRouter (both speak the OpenAI
    Chat Completions shape), so the request/response handling is inherited
    from ``OpenRouterProvider``. Only the default endpoint differs and the
    OpenRouter-specific ``HTTP-Referer`` / ``X-Title`` headers are dropped.
    """

    _label = "OpenAI"

    def __init__(self, base_url: str = OPENAI_DEFAULT_BASE_URL) -> None:
        super().__init__(base_url=base_url)

    def _build_headers(self, api_key: str | None) -> dict:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


def _parse_ollama_response(data: dict) -> ModelResponse:
    """Convert an Ollama /api/chat JSON response to a unified ModelResponse."""
    msg = data.get("message", {})
    blocks: list[ContentBlock] = []

    if msg.get("content"):
        blocks.append(ContentBlock(type="text", text=msg["content"]))

    for tc in msg.get("tool_calls", []):
        func = tc.get("function", {})
        blocks.append(ContentBlock(
            type="tool_use",
            tool_use_id=None,
            tool_name=func.get("name"),
            tool_input=func.get("arguments", {}),
        ))

    stop_reason = "end_turn"
    if msg.get("tool_calls"):
        stop_reason = "tool_use"

    usage_data = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    if "prompt_eval_count" in data:
        usage_data["input_tokens"] = data["prompt_eval_count"]
    if "eval_count" in data:
        usage_data["output_tokens"] = data["eval_count"]

    return ModelResponse(content=blocks, stop_reason=stop_reason, usage=usage_data)


class OllamaProvider:
    """Adapter for the Ollama REST API."""

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)

    async def send(
        self,
        messages: list[dict],
        config: ModelConfig,
        tools: list[dict] | None = None,
        api_key: str | None = None,
    ) -> ModelResponse:
        async def _call():
            try:
                body: dict = {
                    "model": config.id,
                    "messages": messages,
                    "stream": False,
                }
                if tools:
                    body["tools"] = tools
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                resp = await self._client.post(
                    f"{self._base_url}/api/chat", json=body, headers=headers,
                )
                if resp.status_code == 429 or resp.status_code in (500, 502, 503):
                    raise RetryableError(f"Ollama returned {resp.status_code}: {resp.text}")
                resp.raise_for_status()
                return _parse_ollama_response(resp.json())
            except RetryableError:
                raise
            except httpx.ConnectError as exc:
                raise RetryableError(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                raise ProviderError(str(exc)) from exc
            except Exception as exc:
                if isinstance(exc, ProviderError):
                    raise
                raise ProviderError(str(exc)) from exc

        return await _retry_with_backoff(_call)

    async def send_streaming(
        self,
        messages: list[dict],
        config: ModelConfig,
        tools: list[dict] | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        body: dict = {
            "model": config.id,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async def _open_stream():
            try:
                req = self._client.build_request(
                    "POST", f"{self._base_url}/api/chat", json=body, headers=headers,
                )
                resp = await self._client.send(req, stream=True)
                if resp.status_code == 429 or resp.status_code in (500, 502, 503):
                    await resp.aclose()
                    raise RetryableError(f"Ollama returned {resp.status_code}")
                resp.raise_for_status()
                return resp
            except RetryableError:
                raise
            except httpx.ConnectError as exc:
                raise RetryableError(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                raise ProviderError(str(exc)) from exc
            except Exception as exc:
                if isinstance(exc, (ProviderError, RetryableError)):
                    raise
                raise ProviderError(str(exc)) from exc

        resp = await _retry_with_backoff(_open_stream)

        try:
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                data = _json.loads(line)
                if data.get("done"):
                    for tc in data.get("message", {}).get("tool_calls", []):
                        func = tc.get("function", {})
                        yield StreamChunk(
                            type="tool_use",
                            tool_name=func.get("name"),
                            tool_input=func.get("arguments", {}),
                        )
                    stop_reason = "end_turn"
                    if data.get("message", {}).get("tool_calls"):
                        stop_reason = "tool_use"
                    usage = {
                        "input_tokens": data.get("prompt_eval_count", 0),
                        "output_tokens": data.get("eval_count", 0),
                    }
                    yield StreamChunk(type="final", stop_reason=stop_reason, usage=usage)
                else:
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield StreamChunk(type="text_delta", text=content)
        finally:
            await resp.aclose()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_provider(config: ModelConfig) -> ModelProviderProtocol:
    """Return the appropriate provider adapter for *config*.

    Atlas B11 — ``openrouter`` doubles as the generic OpenAI-compatible
    channel: setting ``config.base_url`` redirects it to any OpenAI-style
    upstream (e.g. a LiteLLM gateway or a self-hosted vLLM server) without a
    new adapter. Credential naming follows the provider string, not the
    endpoint (see docs/model-gateway.md).
    """
    if config.provider == "openrouter":
        return OpenRouterProvider(base_url=config.base_url or OPENROUTER_DEFAULT_BASE_URL)
    if config.provider == "openai":
        return OpenAIProvider(base_url=config.base_url or OPENAI_DEFAULT_BASE_URL)
    if config.provider == "ollama":
        return OllamaProvider(base_url=config.base_url or "http://localhost:11434")
    raise ValueError(f"Unknown provider: {config.provider}")
