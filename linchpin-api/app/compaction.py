"""Atlas B1 — context compaction (PRD FR-2.2).

Keeps long-running sessions under the model's context window. When the
stored token estimate for a session reaches ~80% of the model window,
``maybe_compact`` rewrites the covered prefix of the event log into a
single ``session.compacted`` event:

- **Boundary selection** — events are covered up to the turn boundary
  before the last *uncovered* ``user.message`` (its seq − 1), so the
  final turn — possibly mid-flight with dangling tool_use/tool_result
  pairs — is never compacted and tool pairings are never split.
- **Nested coverage** — the effective summary is always the
  ``session.compacted`` event with the largest ``covered_seq_end``. A new
  compaction re-summarizes the previous summary together with the newly
  covered events, so replay stays simple (skip everything ≤ covered_end)
  and summaries never stack.
- **Replay** — ``build_context`` injects the effective summary as a
  ``<session_summary>`` system message and skips every event with
  ``seq <= covered_seq_end``.

Estimation is deliberately cheap: a CJK-aware chars-per-token heuristic
over raw payloads, calibrated after every provider call with real usage
numbers (``sessions.context_estimate``). Compaction is best-effort — any
failure degrades to "no compaction this round" and never breaks the
agent loop.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from app.db import execute, fetch_all, fetch_one
from app.event_crypto import decrypt_payload, get_session_data_key
from app.events import append_event

logger = logging.getLogger("linchpin-api.compaction")

# Compact when the estimate reaches this fraction of the context window.
COMPACT_THRESHOLD = 0.8
# Never bother compacting below this estimate — avoids churn on tiny
# sessions configured with a very small (or absent) window.
MIN_COMPACT_TOKENS = 2000
# A covered interval must contain at least this many events, otherwise
# compaction would trade real history for a summary of almost nothing.
MIN_COVERED_EVENTS = 2
# Fallback when the agent model and the environment provide no window.
DEFAULT_CONTEXT_WINDOW = 128_000


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Rough token estimate: CJK chars ≈ 1 token each, other text ≈ 4 chars/token."""
    if not text:
        return 0
    cjk = 0
    for ch in text:
        code = ord(ch)
        if (
            0x4E00 <= code <= 0x9FFF      # CJK unified ideographs
            or 0x3000 <= code <= 0x303F   # CJK punctuation
            or 0xFF00 <= code <= 0xFFEF   # fullwidth forms / katakana
        ):
            cjk += 1
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def _payload_text(payload: Any) -> str:
    """Render an event payload as plain text for estimation."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(payload)


def get_context_window(agent: Any) -> int:
    """Resolve the model context window: ModelConfig → env → default."""
    model = getattr(agent, "model", None)
    configured = getattr(model, "context_window", None)
    if configured and int(configured) > 0:
        return int(configured)
    env_value = os.environ.get("LINCHPIN_CONTEXT_WINDOW", "")
    if env_value.isdigit() and int(env_value) > 0:
        return int(env_value)
    return DEFAULT_CONTEXT_WINDOW


# ---------------------------------------------------------------------------
# Transcript rendering
# ---------------------------------------------------------------------------


def _render_event_line(seq: int, event_type: str, payload: dict) -> str | None:
    """Render one event as a single transcript line (or None to skip)."""
    if event_type == "user.message":
        content = payload.get("content")
        if content is None or content == "":
            content = payload.get("text", "")
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False, default=str)
        return f"[{seq}] user: {content}"
    if event_type == "agent.message":
        return f"[{seq}] assistant: {payload.get('content', '')}"
    if event_type in ("agent.tool_use", "agent.mcp_tool_use", "agent.custom_tool_use"):
        return (
            f"[{seq}] assistant tool_use {payload.get('name', '')}: "
            f"{json.dumps(payload.get('input', {}), ensure_ascii=False, default=str)}"
        )
    if event_type in ("agent.tool_result", "agent.mcp_tool_result"):
        if "error" in payload:
            body = f"error: {payload['error']}"
        else:
            result = payload.get("result", "")
            if isinstance(result, (dict, list)):
                result = json.dumps(result, ensure_ascii=False, default=str)
            body = str(result)
        return f"[{seq}] tool {payload.get('tool_use_id', '')}: {body}"
    # agent.thinking, spans, status events, etc. — not worth summarizing.
    return None


def render_transcript(events: list) -> str:
    """Render covered events as a lightweight text transcript."""
    lines: list[str] = []
    for e in events:
        event_type = e["type"]
        payload = e["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        line = _render_event_line(e["seq"], event_type, payload)
        if line:
            lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

_SUMMARY_SYSTEM_PROMPT = (
    "You are a context-compaction assistant for an autonomous agent runtime. "
    "Produce a concise but complete summary of the conversation transcript you "
    "are given. Preserve: the user's goals and explicit decisions, key tool "
    "calls and their outcomes, files and resources touched, unresolved tasks, "
    "constraints, and anything the agent still needs to remember to continue "
    "seamlessly. Write the summary as plain prose with short bullet sections. "
    "Do not editorialize and do not add information that is not in the transcript."
)


async def _generate_summary(
    agent: Any,
    provider: Any,
    api_key: str | None,
    transcript: str,
    previous_summary: str | None,
) -> tuple[str, dict]:
    """Generate the compaction summary via a non-streaming provider call.

    Returns ``(summary, usage)``; raises on provider failure so the caller
    can fall back to no compaction.
    """
    sections: list[str] = []
    if previous_summary:
        sections.append(f"## Previous summary\n{previous_summary}")
    sections.append(f"## Conversation history\n{transcript}")

    user_content = (
        "Summarize the following agent conversation history so the agent can "
        "continue seamlessly with the summary replacing the covered history.\n\n"
        + "\n\n".join(sections)
    )
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    response = await provider.send(messages, agent.model, tools=None, api_key=api_key)

    parts = [
        block.text
        for block in (response.content or [])
        if getattr(block, "type", "") == "text" and getattr(block, "text", None)
    ]
    summary = "\n".join(parts).strip()
    if not summary:
        raise ValueError("compaction summary generation returned empty content")
    return summary, dict(getattr(response, "usage", {}) or {})


# ---------------------------------------------------------------------------
# maybe_compact
# ---------------------------------------------------------------------------


async def maybe_compact(
    session_id: str,
    agent: Any,
    provider: Any,
    api_key: str | None = None,
) -> bool:
    """Compact the covered prefix of the event log if the estimate is near
    the model's context window.

    Best-effort by contract: any failure (no DB pool in unit tests,
    provider errors, malformed payloads…) logs a warning and returns
    False — compaction must never break the agent loop.
    """
    try:
        return await _maybe_compact_inner(session_id, agent, provider, api_key)
    except Exception:
        logger.warning(
            "context compaction skipped for session %s", session_id, exc_info=True
        )
        return False


async def _maybe_compact_inner(
    session_id: str,
    agent: Any,
    provider: Any,
    api_key: str | None,
) -> bool:
    uid = uuid.UUID(session_id)

    row = await fetch_one("SELECT * FROM sessions WHERE id = $1", uid)
    if row is None:
        return False
    session = dict(row)  # asyncpg Record has no .get()

    events_raw = await fetch_all(
        "SELECT seq, type, payload FROM events WHERE session_id = $1 ORDER BY seq ASC",
        uid,
    )
    if not events_raw:
        return False

    # Atlas B8 — business-content payloads may be stored encrypted; resolve
    # the data key once and decrypt in place. Control events (session.*) are
    # plaintext and pass through unchanged. Records are immutable, so rows
    # are converted to dicts to carry the decrypted payload downstream.
    data_key = await get_session_data_key(uid)
    events = []
    for e in events_raw:
        row = dict(e)
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        row["payload"] = decrypt_payload(payload, data_key)
        events.append(row)

    # --- 1. Budget estimate -------------------------------------------------
    estimate = int(session.get("context_estimate") or 0)
    if estimate <= 0:
        # Never calibrated (long-lived session resumed after the column was
        # introduced) — fall back to a raw payload scan.
        estimate = sum(estimate_tokens(_payload_text(e["payload"])) for e in events)
    if estimate <= 0:
        return False

    window = get_context_window(agent)
    threshold = int(window * COMPACT_THRESHOLD)
    if estimate < threshold or estimate < MIN_COMPACT_TOKENS:
        return False

    # --- 2. Effective previous compaction -----------------------------------
    prev_covered_end = 0
    prev_summary: str | None = None
    for e in events:
        if e["type"] == "session.compacted":
            payload = e["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            covered_end = int(payload.get("covered_seq_end", 0) or 0)
            if covered_end > prev_covered_end:
                prev_covered_end = covered_end
                prev_summary = payload.get("summary") or None

    # --- 3. Boundary selection ----------------------------------------------
    # Cover up to the turn boundary before the last uncovered user.message:
    # the final turn (possibly mid-flight, with tool pairs) stays untouched.
    boundary = 0
    for e in events:
        if e["seq"] <= prev_covered_end:
            continue
        if e["type"] == "user.message":
            boundary = e["seq"] - 1
    if boundary <= prev_covered_end:
        return False

    covered = [e for e in events if prev_covered_end < e["seq"] <= boundary]
    if len(covered) < MIN_COVERED_EVENTS:
        return False

    # --- 4. Generate the summary ---------------------------------------------
    transcript = render_transcript(covered)
    summary, summary_usage = await _generate_summary(
        agent, provider, api_key, transcript, prev_summary
    )

    # --- 5. Persist the compacted event ---------------------------------------
    await append_event(session_id, "session.compacted", {
        "summary": summary,
        "covered_seq_start": covered[0]["seq"],
        "covered_seq_end": boundary,
        "model": {"provider": agent.model.provider, "id": agent.model.id},
        "usage": summary_usage,
    })

    # --- 6. Recalibrate the estimate for the remaining context ---------------
    # Summary + uncovered tail is what the next build_context will send.
    try:
        remaining_text = summary
        for e in events:
            if e["seq"] > boundary:
                remaining_text += "\n" + _payload_text(e["payload"])
        await execute(
            "UPDATE sessions SET context_estimate = $1, updated_at = now() WHERE id = $2",
            estimate_tokens(remaining_text),
            uid,
        )
    except Exception:
        # The compacted event is already persisted; only the estimate write
        # failed — degrade quietly, next provider call re-calibrates anyway.
        logger.warning(
            "failed to recalibrate context_estimate for session %s",
            session_id, exc_info=True,
        )

    logger.info(
        "session %s compacted: covered seq %d..%d (%d events), summary %d tokens",
        session_id, covered[0]["seq"], boundary, len(covered), estimate_tokens(summary),
    )
    return True
