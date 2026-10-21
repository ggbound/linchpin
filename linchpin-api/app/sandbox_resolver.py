"""Async sandbox resolution across local runtimes and remote executor nodes.

Atlas M4 (FR-4.6) — BYO executor. The synchronous factories in
:mod:`app.sandbox` can't resolve remote sandboxes (they need an
``executor_nodes`` DB lookup plus token decryption), so session-creation
and lifecycle call sites go through this module instead:

- :func:`resolve_sandbox_for_config` — creation path; picks the remote
  executor named by ``env_config["executor_node"]`` or falls back to the
  local docker/microvm runtime.
- :func:`resolve_session_sandbox` — lifecycle path; rebuilds the remote
  sandbox pinned in ``session.metadata`` ("sandbox_runtime" == "remote")
  or routes to the pinned local runtime.

Remote pins use the node *name* (UNIQUE across archived rows too), so
the pin stays resolvable without a second id mapping.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db import fetch_one
from app.sandbox import (
    DockerSandbox,
    MicroVMSandbox,
    RemoteExecutorSandbox,
    SandboxError,
    remote_sandbox_for_node_row,
    resolve_sandbox,
    sandbox_for_session,
)

logger = logging.getLogger("linchpin-api.sandbox-resolver")

_NODE_SELECT = (
    "SELECT id, org_id, name, url, token_encrypted, max_sessions, "
    "last_heartbeat_at, created_at FROM executor_nodes "
    "WHERE archived_at IS NULL AND "
)


async def resolve_sandbox_for_config(
    app_state: Any, env_config: Any
) -> DockerSandbox | MicroVMSandbox | RemoteExecutorSandbox:
    """Pick the sandbox for a session created from *env_config*.

    ``executor_node`` set → look up the live node row and build a remote
    sandbox (rejecting at-capacity nodes); otherwise defer to the
    synchronous local factory. Raises SandboxError when the named node
    doesn't exist or VAULT_ENCRYPTION_KEY is unavailable.
    """
    node_name = (
        env_config.get("executor_node") if isinstance(env_config, dict) else None
    )
    if not node_name:
        return resolve_sandbox(app_state, env_config)

    row = await fetch_one(_NODE_SELECT + "name = $1", node_name)
    if row is None:
        raise SandboxError(f"Executor node '{node_name}' not found")
    await _check_node_capacity(row)
    return remote_sandbox_for_node_row(row)


async def resolve_session_sandbox(
    app_state: Any, session_row: Any
) -> DockerSandbox | MicroVMSandbox | RemoteExecutorSandbox:
    """Pick the sandbox that owns an existing session.

    Sessions pinned to "remote" rebuild their node's sandbox from the
    ``executor_node`` name recorded in metadata at creation time;
    everything else defers to the C3 pin routing in
    :func:`sandbox_for_session`.
    """
    metadata = (
        session_row.get("metadata") if hasattr(session_row, "get") else None
    )
    runtime = metadata.get("sandbox_runtime") if isinstance(metadata, dict) else None
    if runtime != "remote":
        return sandbox_for_session(app_state, session_row)

    node_name = (
        metadata.get("executor_node") if isinstance(metadata, dict) else None
    )
    if not node_name:
        raise SandboxError(
            "session pinned to the remote runtime without an executor node"
        )
    row = await fetch_one(_NODE_SELECT + "name = $1", node_name)
    if row is None:
        raise SandboxError(
            f"Executor node '{node_name}' for this session no longer exists"
        )
    return remote_sandbox_for_node_row(row)


async def _check_node_capacity(row: Any) -> None:
    """Reject new sessions when the node is at its max_sessions cap."""
    live = await fetch_one(
        """
        SELECT count(*) AS n FROM sessions
        WHERE (metadata->>'executor_node') = $1
          AND status NOT IN ('terminated', 'failed')
        """,
        str(row["name"]),
    )
    max_sessions = int(row["max_sessions"] or 0)
    if live is not None and max_sessions > 0 and int(live["n"]) >= max_sessions:
        raise SandboxError(
            f"Executor node '{row['name']}' is at capacity "
            f"({live['n']}/{max_sessions} live sessions)"
        )
