"""Built-in tool implementations for the Linchpin sandbox.

Provides 10 tools: bash, read, write, edit, glob, grep, web_fetch, web_search,
save_artifact, spawn_subagent.
Each tool validates its parameters, executes via the sandbox or httpx, and
returns a result dict.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, FR-9.1, FR-6.1
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

from app.auth import AuthContext
from app.db import fetch_one
from app.files import FileTooLargeError, get_file_store, get_max_bytes
from app.sandbox import DockerSandbox, SandboxError

logger = logging.getLogger("linchpin-api.tools")


async def execute_builtin_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a built-in tool and return the result dict.

    ``context`` (Atlas B5) carries session-scoped attribution —
    ``{"session_id", "project_id"}`` — for tools that register API-side
    resources (``save_artifact`` writes a ``files`` row).
    """
    handler = _TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return {"error": f"Unknown built-in tool: {tool_name}"}
    try:
        return await handler(tool_input, sandbox, container_id, context)
    except SandboxError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.exception("Tool '%s' failed unexpectedly", tool_name)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Individual tool handlers
# ---------------------------------------------------------------------------


async def _bash(
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Req 13.1 — execute a command inside the container."""
    command = tool_input.get("command")
    if not command:
        return {"error": "Missing required parameter: command"}
    timeout = tool_input.get("timeout")
    if timeout is not None:
        command = f"timeout {int(timeout)} {command}"
    result = await sandbox.exec(container_id, command)
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
    }


async def _read(
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Req 13.2 — read a file from the container."""
    path = tool_input.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    content = await sandbox.read_file(container_id, path)
    return {"content": content}


async def _write(
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Req 13.3 — write a file inside the container."""
    path = tool_input.get("path")
    content = tool_input.get("content")
    if not path:
        return {"error": "Missing required parameter: path"}
    if content is None:
        return {"error": "Missing required parameter: content"}
    await sandbox.write_file(container_id, path, content)
    return {"status": "ok"}


async def _edit(
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Req 13.4 — read file, replace old_text with new_text, write back."""
    path = tool_input.get("path")
    old_text = tool_input.get("old_text")
    new_text = tool_input.get("new_text")
    if not path:
        return {"error": "Missing required parameter: path"}
    if old_text is None:
        return {"error": "Missing required parameter: old_text"}
    if new_text is None:
        return {"error": "Missing required parameter: new_text"}

    content = await sandbox.read_file(container_id, path)
    if old_text not in content:
        return {"error": "old_text not found in file"}
    updated = content.replace(old_text, new_text, 1)
    await sandbox.write_file(container_id, path, updated)
    return {"status": "ok"}


async def _glob(
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Req 13.5 — glob/find pattern match inside the container."""
    pattern = tool_input.get("pattern")
    if not pattern:
        return {"error": "Missing required parameter: pattern"}
    path = tool_input.get("path", ".")
    result = await sandbox.exec(container_id, f"find {path} -name '{pattern}'")
    lines = [l for l in result.stdout.strip().splitlines() if l]
    return {"matches": lines}


async def _grep(
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Req 13.6 — text search inside the container using rg."""
    pattern = tool_input.get("pattern")
    if not pattern:
        return {"error": "Missing required parameter: pattern"}
    path = tool_input.get("path", ".")
    include = tool_input.get("include")
    cmd = f"rg --line-number --no-heading '{pattern}' {path}"
    if include:
        cmd += f" --glob '{include}'"
    result = await sandbox.exec(container_id, cmd)
    matches: list[dict[str, Any]] = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        # rg output: file:line:text
        parts = line.split(":", 2)
        if len(parts) >= 3:
            matches.append({"file": parts[0], "line": int(parts[1]), "text": parts[2]})
        else:
            matches.append({"file": "", "line": 0, "text": line})
    return {"matches": matches}


async def _web_fetch(
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Req 13.7 — fetch a URL via httpx (runs in the API process, not sandbox)."""
    url = tool_input.get("url")
    if not url:
        return {"error": "Missing required parameter: url"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
        return {
            "status_code": resp.status_code,
            "body": resp.text,
            "headers": dict(resp.headers),
        }
    except httpx.HTTPError as exc:
        return {"error": f"HTTP request failed: {exc}"}


async def _web_search(
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Web search via Tavily.

    Reads TAVILY_API_KEY from the API process environment. If unset, returns
    a clear configuration error rather than silently failing — same wording
    as before the backend was wired, so existing tests/docs still match.

    Tavily was picked as the default because:
      * Purpose-built for LLM agents (returns clean snippets, not raw HTML)
      * Free tier (1000 searches/month) covers typical agent demos
      * Simple POST/JSON, no SDK needed

    To swap backends, replace this function — the tool registry below
    points at it by name.
    """
    query = tool_input.get("query")
    if not query:
        return {"error": "Missing required parameter: query"}

    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return {
            "error": (
                "web_search backend not configured. Set TAVILY_API_KEY on "
                "linchpin-api, or override _web_search in app/tools.py to "
                "point at a different provider (Brave, SerpAPI, etc.)."
            )
        }

    max_results = tool_input.get("max_results", 5)
    if not isinstance(max_results, int) or max_results < 1 or max_results > 20:
        max_results = 5

    body = {
        "api_key": api_key,
        "query": query,
        "search_depth": tool_input.get("search_depth", "basic"),
        "max_results": max_results,
        "include_answer": True,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.tavily.com/search", json=body)
    except httpx.HTTPError as exc:
        return {"error": f"web_search HTTP error: {exc}"}

    if resp.status_code != 200:
        # Bubble Tavily's error body so the agent can react (rate-limited,
        # bad query, etc.). With the orchestrator's tool-error recovery in
        # place, the agent gets a chance to retry with different args.
        return {
            "error": f"web_search backend returned {resp.status_code}: "
            f"{resp.text[:200]}"
        }

    data = resp.json()
    # Normalize Tavily's response into a minimal shape the model can chew on
    # without provider-specific knowledge.
    results = [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "snippet": r.get("content"),
            "score": r.get("score"),
        }
        for r in data.get("results", [])
    ]
    return {
        "query": query,
        "answer": data.get("answer"),
        "results": results,
    }


async def _save_artifact(
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """FR-9.1 — copy a file out of the container into the file store.

    Binary-safe ``copy_from_container`` → ``FileStore.write`` → ``files`` row
    attributed to the calling session (``source='artifact'``,
    ``scope_type='session'``, ``downloadable=TRUE``). Deduplicated per
    (session, sha256) by the partial unique index from migration 0005 — the
    same index the deliverables watcher relies on — so re-saving identical
    content returns the existing row instead of failing.

    Requires ``context["session_id"]`` for attribution; without it the tool
    cannot know which session owns the artifact and returns an error.
    """
    path = tool_input.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    session_id = (context or {}).get("session_id")
    if not session_id:
        return {"error": "save_artifact requires session context (session_id)"}

    name = tool_input.get("name") or os.path.basename(str(path).rstrip("/")) or "artifact"
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"

    data = await sandbox.copy_from_container(container_id, str(path))

    async def _chunks() -> AsyncIterator[bytes]:
        yield data

    store = get_file_store()
    try:
        storage_path, sha256, size_bytes = await store.write(_chunks(), max_bytes=get_max_bytes())
    except FileTooLargeError as exc:
        return {"error": f"artifact exceeds the {exc.limit_bytes} byte cap"}

    file_id = uuid.uuid4()
    inserted = await fetch_one(
        """
        INSERT INTO files
            (id, project_id, filename, content_type, size_bytes,
             storage_path, sha256, source, downloadable,
             scope_type, scope_id)
        VALUES ($1,
                (SELECT project_id FROM sessions WHERE id = $7),
                $2, $3, $4,
                $5, $6, 'artifact', TRUE,
                'session', $7)
        ON CONFLICT (scope_id, sha256)
            WHERE scope_type = 'session' AND archived_at IS NULL
            DO NOTHING
        RETURNING id
        """,
        file_id,
        name,
        content_type,
        size_bytes,
        storage_path,
        sha256,
        session_id,
    )
    if inserted is None:
        # Dedup hit: identical content already saved to this session. Return
        # the existing row — the artifact is reachable either way.
        existing = await fetch_one(
            """
            SELECT id, filename, size_bytes FROM files
            WHERE scope_type = 'session' AND scope_id = $1 AND sha256 = $2
              AND archived_at IS NULL
            """,
            session_id,
            sha256,
        )
        if existing is not None:
            return {
                "file_id": str(existing["id"]),
                "filename": existing["filename"],
                "size_bytes": existing["size_bytes"],
                "sha256": sha256,
                "path": str(path),
                "deduplicated": True,
            }

    return {
        "file_id": str(inserted["id"]) if inserted is not None else str(file_id),
        "filename": name,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "path": str(path),
    }


async def _spawn_subagent(
    tool_input: dict[str, Any],
    sandbox: DockerSandbox,
    container_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """FR-6.1 — spawn a declared subagent as a worker thread.

    Fail-closed: only names present on the *version-pinned* agent config
    (``load_agent_at_version``) can be spawned — a session can only reach
    workers it declared at boot time. Reuses the HTTP ``spawn_thread``
    machinery (trigger_deployment precedent): a system AuthContext is
    derived from the parent session's project and a synthetic Request
    carries ``app.state`` into the route function. Permission to even
    call this tool is fixed at ``always_ask`` by PolicyEvaluator.
    """
    name = tool_input.get("name")
    input_message = tool_input.get("input_message")
    if not name or not isinstance(name, str):
        return {"error": "Missing required parameter: name"}
    if not input_message or not isinstance(input_message, str):
        return {"error": "Missing required parameter: input_message"}

    session_id = (context or {}).get("session_id")
    if not session_id:
        return {"error": "spawn_subagent requires session context (session_id)"}

    # Lazy imports: app.main/app.routes.sessions/app.orchestrator are heavy
    # modules that (transitively) import this file — importing them at
    # module level would be circular. Call-time imports also keep the
    # test-suite ``patch(...)`` seams working.
    from app.orchestrator import load_agent_at_version

    session = await fetch_one(
        "SELECT id, project_id, agent_id, agent_version, environment_id, title "
        "FROM sessions WHERE id = $1",
        uuid.UUID(str(session_id)),
    )
    if session is None:
        return {"error": f"Session {session_id} not found"}

    try:
        agent = await load_agent_at_version(
            str(session["agent_id"]), session["agent_version"]
        )
    except ValueError as exc:
        return {"error": str(exc)}

    cfg = next((s for s in agent.multi_agents if s.name == name), None)
    if cfg is None:
        # Fail closed: the model can only reach workers declared on the
        # pinned agent snapshot, not arbitrary agent ids.
        declared = sorted(s.name for s in agent.multi_agents)
        return {
            "error": (
                f"Subagent '{name}' is not declared on this agent "
                f"(declared: {', '.join(declared) if declared else 'none'})"
            )
        }

    org_row = await fetch_one(
        "SELECT org_id FROM projects WHERE id = $1",
        uuid.UUID(str(session["project_id"])),
    )
    if org_row is None:
        return {"error": f"Project {session['project_id']} not found"}

    ctx = AuthContext(
        project_id=str(session["project_id"]),
        org_id=str(org_row["org_id"]),
        api_key_id=None,
        scopes=("*",),
    )

    # Synthetic request: spawn_thread → create_session reads
    # request.app.state.* (sandbox, orchestrator_tasks, …). At handler
    # call time ``app.main`` is fully loaded, so the lazy import is safe.
    from app.main import app as linchpin_app

    request = StarletteRequest({
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "app": linchpin_app,
        "state": {},
    })

    from app.routes.sessions import _SpawnThreadRequest, spawn_thread

    body = _SpawnThreadRequest(
        agent_id=str(cfg.agent_id),
        environment_id=str(cfg.environment_id or session["environment_id"]),
        title=f"subagent: {name}",
        metadata={
            "subagent_name": name,
            "parent_session_id": str(session_id),
            "parent_title": session["title"],
        },
        input_message=input_message,
    )
    try:
        new_session = await spawn_thread(
            str(session_id), body, request, ctx
        )  # type: ignore[arg-type]
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("error") or str(detail)
        return {"error": f"spawn_subagent failed: {detail}"}

    return {
        "thread_id": new_session.id,
        "session_id": str(session_id),
        "subagent": name,
        "status": new_session.status,
    }


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_TOOL_HANDLERS = {
    "bash": _bash,
    "read": _read,
    "write": _write,
    "edit": _edit,
    "glob": _glob,
    "grep": _grep,
    "web_fetch": _web_fetch,
    "web_search": _web_search,
    "save_artifact": _save_artifact,
    "spawn_subagent": _spawn_subagent,
}
