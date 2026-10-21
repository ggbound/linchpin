"""Atlas M4 (FR-4.6) — remote executor plumbing tests.

Covers the four seams of BYO-executor:

- ``RemoteExecutorSandbox``: every SandboxProtocol call becomes an
  authenticated HTTP request (verified over a recording MockTransport).
- ``app.sandbox_resolver``: config/session routing between local
  runtimes and named executor nodes (capacity + missing-node errors).
- ``app.executor_daemon``: the standalone daemon endpoints the sandbox
  proxies to (bearer auth, error mapping).
- ``POST /v1/sessions``: creation dispatches to the node and pins
  ``executor_node``/``sandbox_runtime`` in session metadata.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import DEFAULT_ORG_ID
from app.models import EnvironmentPackages, SandboxResources
from app.sandbox import (
    DEFAULT_BASE_IMAGE,
    ExecResult,
    RemoteExecutorSandbox,
    ResourceMount,
    SandboxError,
    remote_sandbox_for_node_row,
)
from app.sandbox_resolver import (
    resolve_sandbox_for_config,
    resolve_session_sandbox,
)


NODE_URL = "http://10.0.0.8:8100"
NODE_TOKEN = "lex_nodetoken0123456789abcdef0123456789abcdef0123456"
APP_STATE = object()  # opaque marker passed through to local factories

AUTH = {"Authorization": "Bearer test-secret-key"}


# ---------------------------------------------------------------------------
# Shared row helpers
# ---------------------------------------------------------------------------


def _make_agent_row(*, agent_id: str | None = None):
    return {
        "id": uuid.UUID(agent_id) if agent_id else uuid.uuid4(),
        "name": "test-agent",
        "version": 1,
        "model": {
            "provider": "openrouter",
            "id": "anthropic/claude-sonnet-4",
            "base_url": None,
        },
        "system": "You are helpful.",
        "tools": [],
        "mcp_servers": [],
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }


def _make_session_row(
    *,
    agent_id: str | None = None,
    environment_id: str | None = None,
    container_id: str = "container-abc",
    metadata: dict | None = None,
):
    return {
        "id": uuid.uuid4(),
        "agent_id": uuid.UUID(agent_id) if agent_id else uuid.uuid4(),
        "agent_version": 1,
        "environment_id": (
            uuid.UUID(environment_id) if environment_id else uuid.uuid4()
        ),
        "status": "running",
        "container_id": container_id,
        "title": None,
        "metadata": metadata or {},
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "archived_at": None,
        "last_event_cursor": None,
        "ttl_seconds": None,
        "stats": {"total_events": 0, "tool_calls": 0, "model_turns": 0},
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "vault_ids": [],
    }


# ---------------------------------------------------------------------------
# (a) RemoteExecutorSandbox over a recording MockTransport
# ---------------------------------------------------------------------------


def _make_sandbox(handler):
    """RemoteExecutorSandbox wired to a recording httpx.MockTransport."""
    requests: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(_record)
    sandbox = RemoteExecutorSandbox(NODE_URL, NODE_TOKEN, transport=transport)
    return sandbox, requests


@pytest.mark.asyncio
async def test_create_posts_provisioning_payload():
    sandbox, requests = _make_sandbox(
        lambda req: httpx.Response(200, json={"container_id": "c-1"})
    )

    container_id = await sandbox.create(
        "ubuntu:24.04", "linchpin-open", env={"FOO": "bar"}
    )

    assert container_id == "c-1"
    req = requests[0]
    assert (req.method, str(req.url)) == (
        "POST", f"{NODE_URL}/v1/containers",
    )
    assert req.headers["Authorization"] == f"Bearer {NODE_TOKEN}"
    body = json.loads(req.content)
    assert body == {
        "image": "ubuntu:24.04",
        "network": "linchpin-open",
        "env": {"FOO": "bar"},
        "files": [],
    }
    assert "resources" not in body  # omitted when the caller sends none


@pytest.mark.asyncio
async def test_create_sends_resource_tier():
    sandbox, requests = _make_sandbox(
        lambda req: httpx.Response(200, json={"container_id": "c-1"})
    )

    await sandbox.create(
        "ubuntu:24.04", "linchpin-open",
        resources=SandboxResources(tier="2c4g"),
    )

    assert json.loads(requests[0].content)["resources"] == {"tier": "2c4g"}


@pytest.mark.asyncio
async def test_create_packs_single_file_mount(tmp_path):
    seed = tmp_path / "seed.txt"
    seed.write_bytes(b"hello-mount")
    sandbox, requests = _make_sandbox(
        lambda req: httpx.Response(200, json={"container_id": "c-1"})
    )
    mount = ResourceMount(
        host_path=str(seed), container_path="/opt/seed.txt", mode="ro"
    )

    await sandbox.create("ubuntu:24.04", "linchpin-none", mounts=[mount])

    body = json.loads(requests[0].content)
    assert body["files"] == [{
        "path": "/opt/seed.txt",
        "content_b64": base64.b64encode(b"hello-mount").decode("ascii"),
    }]
    # Read-only mounts are never flushed back on destroy.
    assert sandbox._rw_mounts == {}


@pytest.mark.asyncio
async def test_create_packs_directory_mount_and_records_rw(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.txt").write_bytes(b"top")
    (tmp_path / "sub" / "nested.txt").write_bytes(b"nested")
    sandbox, requests = _make_sandbox(
        lambda req: httpx.Response(200, json={"container_id": "c-9"})
    )
    mount = ResourceMount(
        host_path=str(tmp_path), container_path="/data/", mode="rw"
    )

    await sandbox.create("ubuntu:24.04", "linchpin-none", mounts=[mount])

    # Dict comparison — os.walk order is not part of the contract.
    body = json.loads(requests[0].content)
    packed = {
        f["path"]: base64.b64decode(f["content_b64"]) for f in body["files"]
    }
    assert packed == {
        "/data/top.txt": b"top",
        "/data/sub/nested.txt": b"nested",
    }
    # rw mounts are recorded for the destroy-time flush.
    assert sandbox._rw_mounts["c-9"] == [mount]


@pytest.mark.asyncio
async def test_create_without_container_id_raises():
    sandbox, _ = _make_sandbox(
        lambda req: httpx.Response(200, json={"ok": True})
    )

    with pytest.raises(SandboxError, match="no container_id"):
        await sandbox.create("ubuntu:24.04", "linchpin-none")


@pytest.mark.asyncio
async def test_ensure_image_passthrough_and_package_rejection():
    sandbox, requests = _make_sandbox(lambda req: httpx.Response(200))

    assert await sandbox.ensure_image(
        base_image="ubuntu:24.04", packages=None
    ) == "ubuntu:24.04"
    assert await sandbox.ensure_image(
        base_image="ubuntu:24.04", packages=EnvironmentPackages()
    ) == "ubuntu:24.04"
    with pytest.raises(SandboxError, match="packages are not supported"):
        await sandbox.ensure_image(
            base_image="ubuntu:24.04",
            packages=EnvironmentPackages(apt=["curl"]),
        )
    # The image check never touches the daemon.
    assert requests == []


@pytest.mark.asyncio
async def test_exec_maps_daemon_fields():
    sandbox, requests = _make_sandbox(
        lambda req: httpx.Response(
            200, json={"stdout": "out", "stderr": "err", "exit_code": 7}
        )
    )

    result = await sandbox.exec("c-9", "ls -la")

    assert result == ExecResult(stdout="out", stderr="err", exit_code=7)
    req = requests[0]
    assert (req.method, req.url.path) == (
        "POST", "/v1/containers/c-9/exec",
    )
    assert json.loads(req.content) == {"command": "ls -la"}


@pytest.mark.asyncio
async def test_http_error_maps_to_sandbox_error():
    sandbox, _ = _make_sandbox(
        lambda req: httpx.Response(404, text="no such container")
    )

    with pytest.raises(
        SandboxError, match="returned 404 for POST /v1/containers/c-9/exec"
    ):
        await sandbox.exec("c-9", "ls")


@pytest.mark.asyncio
async def test_transport_error_maps_to_unreachable():
    def _boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    sandbox, _ = _make_sandbox(_boom)

    with pytest.raises(
        SandboxError, match="unreachable at http://10.0.0.8:8100"
    ):
        await sandbox.exec("c-9", "ls")


@pytest.mark.asyncio
async def test_kill_exec_reports_flag():
    sandbox, requests = _make_sandbox(
        lambda req: httpx.Response(200, json={"killed": True})
    )

    assert await sandbox.kill_exec("c-9", 42) is True
    assert json.loads(requests[0].content)["pid"] == 42


@pytest.mark.asyncio
async def test_kill_exec_failure_returns_false():
    sandbox, _ = _make_sandbox(lambda req: httpx.Response(500, text="gone"))

    assert await sandbox.kill_exec("c-9", 42) is False


@pytest.mark.asyncio
async def test_write_file_puts_utf8_body():
    sandbox, requests = _make_sandbox(
        lambda req: httpx.Response(200, json={"status": "written"})
    )

    await sandbox.write_file("c-9", "/tmp/note.txt", "héllo")

    req = requests[0]
    assert (req.method, req.url.path) == ("PUT", "/v1/containers/c-9/files")
    assert req.url.params["path"] == "/tmp/note.txt"
    assert req.content == "héllo".encode("utf-8")


@pytest.mark.asyncio
async def test_read_file_and_copy_from_container():
    sandbox, requests = _make_sandbox(
        lambda req: httpx.Response(200, content=b"raw-bytes")
    )

    assert await sandbox.read_file("c-9", "/tmp/note.txt") == "raw-bytes"
    assert await sandbox.copy_from_container(
        "c-9", "/tmp/note.txt"
    ) == b"raw-bytes"
    assert [r.url.params["path"] for r in requests] == [
        "/tmp/note.txt", "/tmp/note.txt",
    ]


@pytest.mark.asyncio
async def test_list_dir_returns_entries():
    sandbox, requests = _make_sandbox(
        lambda req: httpx.Response(
            200, json={"entries": [{"name": "a.txt", "type": "file"}]}
        )
    )

    entries = await sandbox.list_dir("c-9", "/opt")

    assert entries == [{"name": "a.txt", "type": "file"}]
    req = requests[0]
    assert (req.method, req.url.path) == ("GET", "/v1/containers/c-9/tree")
    assert req.url.params["path"] == "/opt"


def _flush_handler(req: httpx.Request) -> httpx.Response:
    if req.method == "POST":
        return httpx.Response(200, json={"container_id": "c-9"})
    if req.method == "GET" and req.url.path.endswith("/tree"):
        return httpx.Response(
            200, json={"entries": [{"name": "result.txt", "type": "file"}]}
        )
    if req.method == "GET":
        return httpx.Response(200, content=b"result-bytes")
    return httpx.Response(200, json={"status": "destroyed"})


@pytest.mark.asyncio
async def test_destroy_flushes_rw_mounts_before_delete(tmp_path):
    # Host file starts with different content; the flush must overwrite it
    # with the bytes copied back from the daemon.
    (tmp_path / "result.txt").write_bytes(b"seed")
    sandbox, requests = _make_sandbox(_flush_handler)
    mount = ResourceMount(
        host_path=str(tmp_path),
        container_path="/mnt/session/outputs",
        mode="rw",
    )
    await sandbox.create("ubuntu:24.04", "linchpin-none", mounts=[mount])
    requests.clear()

    await sandbox.destroy("c-9")

    # tree listing -> file copy -> container delete, in that order.
    assert [r.method for r in requests] == ["GET", "GET", "DELETE"]
    assert requests[0].url.params["path"] == "/mnt/session/outputs"
    assert requests[1].url.params["path"] == (
        "/mnt/session/outputs/result.txt"
    )
    assert requests[2].url.path == "/v1/containers/c-9"
    assert (tmp_path / "result.txt").read_bytes() == b"result-bytes"
    assert sandbox._rw_mounts == {}


@pytest.mark.asyncio
async def test_destroy_without_mounts_only_deletes():
    sandbox, requests = _make_sandbox(
        lambda req: httpx.Response(200, json={"status": "destroyed"})
    )

    await sandbox.destroy("c-9")

    assert [r.method for r in requests] == ["DELETE"]


@pytest.mark.asyncio
async def test_node_row_without_encryption_raises():
    with patch(
        "app.encryption.get_encryption_service",
        side_effect=RuntimeError("VAULT_ENCRYPTION_KEY is not configured"),
    ):
        with pytest.raises(SandboxError, match="VAULT_ENCRYPTION_KEY"):
            remote_sandbox_for_node_row({
                "url": NODE_URL,
                "token_encrypted": "enc:tok",
            })


# ---------------------------------------------------------------------------
# (b) app.sandbox_resolver — remote vs local routing
# ---------------------------------------------------------------------------


def _node_row(**overrides):
    """executor_nodes row (the resolver's _NODE_SELECT column shape)."""
    row = {
        "id": uuid.uuid4(),
        "org_id": uuid.UUID(DEFAULT_ORG_ID),
        "name": "gpu-box-1",
        "url": NODE_URL,
        "token_encrypted": "enc:tok",
        "max_sessions": 5,
        "last_heartbeat_at": None,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_local_config_defers_to_sync_factory():
    sentinel = object()
    with (
        patch(
            "app.sandbox_resolver.resolve_sandbox", return_value=sentinel
        ) as mock_resolve,
        patch(
            "app.sandbox_resolver.fetch_one", new_callable=AsyncMock
        ) as mock_fetch,
    ):
        result = await resolve_sandbox_for_config(
            APP_STATE, {"networking": {"type": "none"}}
        )

    assert result is sentinel
    mock_resolve.assert_called_once_with(
        APP_STATE, {"networking": {"type": "none"}}
    )
    mock_fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_config_builds_named_node_sandbox():
    node = _node_row()
    sentinel = object()
    with (
        patch(
            "app.sandbox_resolver.fetch_one",
            new_callable=AsyncMock,
            side_effect=[node, {"n": 1}],  # node row, then capacity count
        ) as mock_fetch,
        patch(
            "app.sandbox_resolver.remote_sandbox_for_node_row",
            return_value=sentinel,
        ) as mock_build,
    ):
        result = await resolve_sandbox_for_config(
            APP_STATE, {"executor_node": "gpu-box-1"}
        )

    assert result is sentinel
    node_call = mock_fetch.await_args_list[0]
    assert "name = $1" in node_call.args[0]
    assert node_call.args[1] == "gpu-box-1"
    mock_build.assert_called_once_with(node)


@pytest.mark.asyncio
async def test_unknown_node_raises():
    with patch(
        "app.sandbox_resolver.fetch_one",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with pytest.raises(
            SandboxError, match="Executor node 'gpu-box-1' not found"
        ):
            await resolve_sandbox_for_config(
                APP_STATE, {"executor_node": "gpu-box-1"}
            )


@pytest.mark.asyncio
async def test_node_at_capacity_raises():
    node = _node_row(max_sessions=2)
    with patch(
        "app.sandbox_resolver.fetch_one",
        new_callable=AsyncMock,
        side_effect=[node, {"n": 2}],
    ):
        with pytest.raises(
            SandboxError, match=r"at capacity \(2/2 live sessions\)"
        ):
            await resolve_sandbox_for_config(
                APP_STATE, {"executor_node": "gpu-box-1"}
            )


@pytest.mark.asyncio
async def test_local_session_falls_through_to_pin_routing():
    session_row = {"metadata": {"sandbox_runtime": "docker"}}
    sentinel = object()
    with patch(
        "app.sandbox_resolver.sandbox_for_session",
        return_value=sentinel,
    ) as mock_local:
        result = await resolve_session_sandbox(APP_STATE, session_row)

    assert result is sentinel
    mock_local.assert_called_once_with(APP_STATE, session_row)


@pytest.mark.asyncio
async def test_remote_session_without_node_name_raises():
    session_row = {"metadata": {"sandbox_runtime": "remote"}}

    with pytest.raises(SandboxError, match="without an executor node"):
        await resolve_session_sandbox(APP_STATE, session_row)


@pytest.mark.asyncio
async def test_remote_session_with_gone_node_raises():
    session_row = {
        "metadata": {
            "sandbox_runtime": "remote",
            "executor_node": "gpu-box-1",
        }
    }
    with patch(
        "app.sandbox_resolver.fetch_one",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with pytest.raises(SandboxError, match="no longer exists"):
            await resolve_session_sandbox(APP_STATE, session_row)


@pytest.mark.asyncio
async def test_remote_session_rebuilds_node_sandbox():
    node = _node_row()
    session_row = {
        "metadata": {
            "sandbox_runtime": "remote",
            "executor_node": "gpu-box-1",
        }
    }
    sentinel = object()
    with (
        patch(
            "app.sandbox_resolver.fetch_one",
            new_callable=AsyncMock,
            return_value=node,
        ) as mock_fetch,
        patch(
            "app.sandbox_resolver.remote_sandbox_for_node_row",
            return_value=sentinel,
        ) as mock_build,
    ):
        result = await resolve_session_sandbox(APP_STATE, session_row)

    assert result is sentinel
    # No capacity check on the lifecycle path — a single node lookup only.
    assert mock_fetch.await_count == 1
    assert "name = $1" in mock_fetch.await_args.args[0]
    assert mock_fetch.await_args.args[1] == "gpu-box-1"
    mock_build.assert_called_once_with(node)


# ---------------------------------------------------------------------------
# (c) app.executor_daemon — standalone daemon endpoints
# ---------------------------------------------------------------------------


DAEMON_AUTH = {"Authorization": "Bearer lex_daemon_token"}


@pytest.fixture()
def daemon_client(monkeypatch):
    """TestClient over the standalone daemon app with a mocked sandbox."""
    monkeypatch.setenv("LINCHPIN_EXECUTOR_TOKEN", "lex_daemon_token")
    with (
        patch("app.executor_daemon.DockerSandbox") as MockSandbox,
        patch(
            "app.executor_daemon.ensure_docker_networks",
            new_callable=AsyncMock,
        ),
        patch(
            "app.executor_daemon._inject_files", new_callable=AsyncMock
        ) as mock_inject,
    ):
        sandbox = MagicMock()
        sandbox.create = AsyncMock(return_value="container-daemon")
        sandbox.destroy = AsyncMock()
        sandbox.exec = AsyncMock(
            return_value=ExecResult(stdout="out", stderr="err", exit_code=0)
        )
        sandbox.kill_exec = AsyncMock(return_value=True)
        sandbox.copy_from_container = AsyncMock(return_value=b"file-bytes")
        sandbox.list_dir = AsyncMock(
            return_value=[{"name": "a.txt", "type": "file"}]
        )
        MockSandbox.return_value = sandbox

        from app.executor_daemon import app as daemon_app

        with TestClient(daemon_app) as c:
            yield c, sandbox, mock_inject


def test_ping_is_unauthenticated(daemon_client):
    client, _, _ = daemon_client
    resp = client.get("/v1/ping")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_container_requires_token(daemon_client):
    client, _, _ = daemon_client
    payload = {"image": "ubuntu:24.04", "network": "linchpin-open"}

    missing = client.post("/v1/containers", json=payload)
    wrong = client.post(
        "/v1/containers", json=payload,
        headers={"Authorization": "Bearer lex_wrong_token"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_create_container_503_when_token_unconfigured(
    daemon_client, monkeypatch
):
    client, _, _ = daemon_client
    monkeypatch.delenv("LINCHPIN_EXECUTOR_TOKEN")

    resp = client.post(
        "/v1/containers",
        json={"image": "ubuntu:24.04", "network": "linchpin-open"},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "executor_token_unconfigured"


def test_create_container_returns_201(daemon_client):
    client, sandbox, _ = daemon_client

    resp = client.post(
        "/v1/containers",
        json={
            "image": "ubuntu:24.04",
            "network": "linchpin-open",
            "env": {"FOO": "bar"},
            "resources": {"tier": "2c4g"},
        },
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 201
    assert resp.json() == {"container_id": "container-daemon"}
    sandbox.create.assert_awaited_once_with(
        "ubuntu:24.04",
        "linchpin-open",
        mounts=None,
        env={"FOO": "bar"},
        resources=SandboxResources(tier="2c4g"),
    )


def test_create_container_without_env_passes_none(daemon_client):
    client, sandbox, _ = daemon_client

    resp = client.post(
        "/v1/containers",
        json={"image": "ubuntu:24.04", "network": "linchpin-open"},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 201
    assert sandbox.create.await_args.kwargs["env"] is None
    assert sandbox.create.await_args.kwargs["resources"] is None


def test_create_container_sandbox_error_returns_502(daemon_client):
    client, sandbox, _ = daemon_client
    sandbox.create = AsyncMock(side_effect=SandboxError("docker daemon down"))

    resp = client.post(
        "/v1/containers",
        json={"image": "ubuntu:24.04", "network": "linchpin-open"},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "docker daemon down"


def test_create_container_injects_files(daemon_client):
    client, _, mock_inject = daemon_client

    resp = client.post(
        "/v1/containers",
        json={
            "image": "ubuntu:24.04",
            "network": "linchpin-open",
            "files": [{
                "path": "/opt/seed.txt",
                "content_b64": base64.b64encode(b"seed").decode("ascii"),
            }],
        },
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 201
    mock_inject.assert_awaited_once()
    entry = mock_inject.await_args.args[2][0]
    assert entry.path == "/opt/seed.txt"
    assert base64.b64decode(entry.content_b64) == b"seed"


def test_exec_endpoint_maps_fields(daemon_client):
    client, sandbox, _ = daemon_client

    resp = client.post(
        "/v1/containers/container-daemon/exec",
        json={"command": "ls"},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {"stdout": "out", "stderr": "err", "exit_code": 0}
    sandbox.exec.assert_awaited_once_with("container-daemon", "ls")


def test_exec_sandbox_error_returns_502(daemon_client):
    client, sandbox, _ = daemon_client
    sandbox.exec = AsyncMock(side_effect=SandboxError("container gone"))

    resp = client.post(
        "/v1/containers/container-daemon/exec",
        json={"command": "ls"},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 502


def test_kill_endpoint_returns_flag(daemon_client):
    client, sandbox, _ = daemon_client

    resp = client.post(
        "/v1/containers/container-daemon/kill",
        json={"pid": 42},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {"killed": True}
    assert sandbox.kill_exec.await_args.args == ("container-daemon", 42)


def test_kill_sandbox_error_reports_false(daemon_client):
    client, sandbox, _ = daemon_client
    sandbox.kill_exec = AsyncMock(side_effect=SandboxError("nope"))

    resp = client.post(
        "/v1/containers/container-daemon/kill",
        json={"pid": 42},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {"killed": False}


def test_write_file_wraps_body_as_entry(daemon_client):
    client, _, mock_inject = daemon_client

    resp = client.put(
        "/v1/containers/container-daemon/files",
        params={"path": "/opt/x.txt"},
        content=b"payload",
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "written"}
    entry = mock_inject.await_args.args[2][0]
    assert entry.path == "/opt/x.txt"
    assert base64.b64decode(entry.content_b64) == b"payload"


def test_write_file_rejects_relative_path(daemon_client):
    client, _, mock_inject = daemon_client

    resp = client.put(
        "/v1/containers/container-daemon/files",
        params={"path": "rel/x.txt"},
        content=b"payload",
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 400
    mock_inject.assert_not_awaited()


def test_write_file_inject_error_returns_502(daemon_client):
    client, _, mock_inject = daemon_client
    mock_inject.side_effect = SandboxError("inject failed")

    resp = client.put(
        "/v1/containers/container-daemon/files",
        params={"path": "/opt/x.txt"},
        content=b"payload",
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 502


def test_read_file_returns_bytes(daemon_client):
    client, sandbox, _ = daemon_client

    resp = client.get(
        "/v1/containers/container-daemon/files",
        params={"path": "/opt/x.txt"},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 200
    assert resp.content == b"file-bytes"
    sandbox.copy_from_container.assert_awaited_once_with(
        "container-daemon", "/opt/x.txt"
    )


def test_read_file_error_returns_404(daemon_client):
    client, sandbox, _ = daemon_client
    sandbox.copy_from_container = AsyncMock(
        side_effect=SandboxError("no such file")
    )

    resp = client.get(
        "/v1/containers/container-daemon/files",
        params={"path": "/opt/x.txt"},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 404


def test_list_tree_returns_entries(daemon_client):
    client, sandbox, _ = daemon_client

    resp = client.get(
        "/v1/containers/container-daemon/tree",
        params={"path": "/opt"},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {"entries": [{"name": "a.txt", "type": "file"}]}
    sandbox.list_dir.assert_awaited_once_with("container-daemon", "/opt")


def test_list_tree_error_returns_404(daemon_client):
    client, sandbox, _ = daemon_client
    sandbox.list_dir = AsyncMock(side_effect=SandboxError("no such dir"))

    resp = client.get(
        "/v1/containers/container-daemon/tree",
        params={"path": "/opt"},
        headers=DAEMON_AUTH,
    )

    assert resp.status_code == 404


def test_destroy_container(daemon_client):
    client, sandbox, _ = daemon_client

    resp = client.delete(
        "/v1/containers/container-daemon", headers=DAEMON_AUTH
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "destroyed"}
    sandbox.destroy.assert_awaited_once_with("container-daemon")


def test_destroy_sandbox_error_returns_502(daemon_client):
    client, sandbox, _ = daemon_client
    sandbox.destroy = AsyncMock(side_effect=SandboxError("already gone"))

    resp = client.delete(
        "/v1/containers/container-daemon", headers=DAEMON_AUTH
    )

    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# (d) POST /v1/sessions — remote dispatch + metadata pin
# ---------------------------------------------------------------------------


@pytest.fixture()
def remote_client(tmp_path):
    """sandbox_client clone exposing the local and a remote sandbox mock."""
    from pathlib import Path

    def _fake_ensure(sid):
        p = Path(tmp_path) / "session-outputs" / sid
        p.mkdir(parents=True, exist_ok=True)
        return p

    with (
        patch("app.main.check_migrations_current"),
        patch("app.main.create_pool", new_callable=AsyncMock),
        patch("app.main.close_pool", new_callable=AsyncMock),
        patch("app.main.ensure_docker_networks", new_callable=AsyncMock),
        patch("app.main.DockerSandbox") as MockSandbox,
        patch("app.main.cleanup_expired_sessions", new_callable=AsyncMock),
        patch("app.main.recover_sessions", new_callable=AsyncMock),
        patch("app.routes.sessions.run_session", new_callable=AsyncMock),
        patch(
            "app.routes.sessions.ensure_session_outputs_dir",
            side_effect=_fake_ensure,
        ),
        patch(
            "app.routes.sessions.watch_session_deliverables",
            new_callable=AsyncMock,
        ),
        patch("app.routes.sessions.execute", new_callable=AsyncMock),
        patch("app.routes.sessions.transition", new_callable=AsyncMock),
        patch("app.routes.sessions.append_event", new_callable=AsyncMock),
    ):
        mock_local = MagicMock()
        mock_local.create = AsyncMock(return_value="container-local")
        mock_local.destroy = AsyncMock()
        mock_local.ensure_image = AsyncMock(
            side_effect=lambda *, base_image, packages: base_image
        )
        MockSandbox.return_value = mock_local

        mock_remote = MagicMock()
        mock_remote.create = AsyncMock(return_value="container-remote")
        mock_remote.destroy = AsyncMock()
        mock_remote.ensure_image = AsyncMock(
            side_effect=lambda *, base_image, packages: base_image
        )

        from app.main import app

        with TestClient(app) as c:
            yield c, mock_local, mock_remote


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_create_session_remote_node_dispatches_to_executor(
    mock_fetch, remote_client
):
    client, mock_local, mock_remote = remote_client
    agent_id = str(uuid.uuid4())
    env_id = str(uuid.uuid4())
    agent_row = _make_agent_row(agent_id=agent_id)
    env_row = {
        "id": uuid.UUID(env_id),
        "name": "gpu-env",
        "config": {
            "networking": {"type": "none"},
            "executor_node": "gpu-box-1",
        },
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    session_row = _make_session_row(
        agent_id=agent_id,
        environment_id=env_id,
        container_id="container-remote",
    )

    captured = {}

    async def _fake_fetch(sql, *args):
        if "INSERT INTO sessions" in sql:
            captured["insert_args"] = args
        if "FROM agents" in sql:
            return agent_row
        if "FROM environments" in sql:
            return env_row
        return session_row  # post-transition refresh

    mock_fetch.side_effect = _fake_fetch

    with patch(
        "app.routes.sessions.resolve_sandbox_for_config",
        new_callable=AsyncMock,
        return_value=mock_remote,
    ):
        resp = client.post(
            "/v1/sessions",
            json={"agent_id": agent_id, "environment_id": env_id},
            headers=AUTH,
        )

    assert resp.status_code == 201
    # The pin lands in the INSERT's metadata column ($7 -> args[6]).
    metadata = json.loads(captured["insert_args"][6])
    assert metadata["executor_node"] == "gpu-box-1"
    assert metadata["sandbox_runtime"] == "remote"
    # Provisioning went to the node, not the local Docker daemon.
    assert mock_remote.create.await_args.args == (
        DEFAULT_BASE_IMAGE, "linchpin-none",
    )
    mock_local.create.assert_not_called()


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_create_session_unknown_node_returns_503(mock_fetch, remote_client):
    client, mock_local, _ = remote_client
    agent_id = str(uuid.uuid4())
    env_id = str(uuid.uuid4())
    mock_fetch.side_effect = [
        _make_agent_row(agent_id=agent_id),
        {
            "id": uuid.UUID(env_id),
            "name": "ghost-env",
            "config": {
                "networking": {"type": "none"},
                "executor_node": "ghost-node",
            },
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
    ]

    with patch(
        "app.routes.sessions.resolve_sandbox_for_config",
        new_callable=AsyncMock,
        side_effect=SandboxError("Executor node 'ghost-node' not found"),
    ):
        resp = client.post(
            "/v1/sessions",
            json={"agent_id": agent_id, "environment_id": env_id},
            headers=AUTH,
        )

    assert resp.status_code == 503
    assert resp.json()["error"] == "sandbox_runtime_unavailable"
    assert "ghost-node" in resp.json()["message"]
    # Fails before the INSERT — only the agent + env lookups ran.
    assert mock_fetch.await_count == 2
    mock_local.create.assert_not_called()
