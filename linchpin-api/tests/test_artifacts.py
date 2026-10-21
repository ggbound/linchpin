"""Tests for Atlas B5 — Artifacts & filesystem browsing.

Covers:
- ``_save_artifact`` tool handler — happy path, validation errors, size cap,
  dedup via the (session, sha256) partial unique index.
- ``GET /v1/artifacts`` — source='artifact' filter, optional session_id
  narrowing, pagination.
- ``GET /v1/artifacts/{id}`` + ``/content`` — metadata, streaming, 404s.
- ``GET /v1/sessions/{id}/filesystem`` + ``/raw`` — sandbox listing /
  binary-safe download, project scoping, no-container 409, path validation,
  SandboxError → 400.

Requirements: FR-9.1, FR-9.2
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sandbox import SandboxError
from app.tools import execute_builtin_tool


AUTH = {"Authorization": "Bearer test-secret-key"}

CONTAINER_ID = "container-test-123"

SESSION_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sandbox(**overrides) -> AsyncMock:
    sandbox = AsyncMock()
    sandbox.copy_from_container = AsyncMock(return_value=b"artifact bytes")
    for k, v in overrides.items():
        setattr(sandbox, k, v)
    return sandbox


def _make_file_row(
    *,
    file_id: str | None = None,
    filename: str = "result.csv",
    content_type: str = "text/csv",
    size_bytes: int = 128,
    source: str = "artifact",
    downloadable: bool = True,
    scope_id: str | None = SESSION_ID,
):
    return {
        "id": uuid.UUID(file_id) if file_id else uuid.uuid4(),
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "storage_path": "ab/cd/" + "ab" * 32,
        "sha256": "ab" * 32,
        "source": source,
        "downloadable": downloadable,
        "scope_type": "session" if scope_id else None,
        "scope_id": uuid.UUID(scope_id) if scope_id else None,
        "created_at": datetime(2026, 5, 13, tzinfo=timezone.utc),
        "archived_at": None,
    }


def _make_session_row(container_id: str | None = CONTAINER_ID):
    return {
        "id": uuid.UUID(SESSION_ID),
        "project_id": uuid.UUID(PROJECT_ID),
        "container_id": container_id,
        "status": "running",
    }


# ---------------------------------------------------------------------------
# _save_artifact tool handler
# ---------------------------------------------------------------------------


class TestSaveArtifactTool:
    @pytest.mark.asyncio
    async def test_saves_file_and_inserts_row(self):
        """Happy path: copy bytes out, write through FileStore, INSERT files row."""
        file_uid = uuid.uuid4()
        inserted = {"id": file_uid}
        sandbox = _make_sandbox()
        store = MagicMock()
        store.write = AsyncMock(return_value=("aa/bb/" + "a" * 32, "ab" * 32, 14))

        with (
            patch("app.tools.fetch_one", new_callable=AsyncMock) as mock_fetch,
            patch("app.tools.get_file_store", return_value=store),
            patch("app.tools.get_max_bytes", return_value=1_000_000),
        ):
            mock_fetch.return_value = inserted
            result = await execute_builtin_tool(
                "save_artifact",
                {"path": "/mnt/session/outputs/result.csv"},
                sandbox,
                CONTAINER_ID,
                context={"session_id": SESSION_ID, "project_id": PROJECT_ID},
            )

        assert "error" not in result
        assert result["file_id"] == str(file_uid)
        assert result["filename"] == "result.csv"
        assert result["size_bytes"] == 14
        assert result["sha256"] == "ab" * 32
        assert result["path"] == "/mnt/session/outputs/result.csv"
        sandbox.copy_from_container.assert_called_once_with(
            CONTAINER_ID, "/mnt/session/outputs/result.csv"
        )
        store.write.assert_awaited_once()

        # INSERT attributes via subquery on the session + artifact source.
        sql = mock_fetch.await_args.args[0]
        assert "'artifact'" in sql
        assert "(SELECT project_id FROM sessions WHERE id = $7)" in sql
        assert "scope_type" in sql and "'session'" in sql
        # $7 = session_id
        assert str(mock_fetch.await_args.args[7]) == SESSION_ID

    @pytest.mark.asyncio
    async def test_missing_path(self):
        sandbox = _make_sandbox()
        result = await execute_builtin_tool(
            "save_artifact", {}, sandbox, CONTAINER_ID,
            context={"session_id": SESSION_ID},
        )
        assert "error" in result
        assert "path" in result["error"]

    @pytest.mark.asyncio
    async def test_requires_session_context(self):
        sandbox = _make_sandbox()
        result = await execute_builtin_tool(
            "save_artifact", {"path": "/tmp/a.txt"}, sandbox, CONTAINER_ID,
            context=None,
        )
        assert "error" in result
        assert "session context" in result["error"]

    @pytest.mark.asyncio
    async def test_too_large_returns_error(self):
        from app.files import FileTooLargeError

        sandbox = _make_sandbox()
        store = MagicMock()
        store.write = AsyncMock(side_effect=FileTooLargeError(500))

        with (
            patch("app.tools.get_file_store", return_value=store),
            patch("app.tools.get_max_bytes", return_value=500),
        ):
            result = await execute_builtin_tool(
                "save_artifact", {"path": "/tmp/big.bin"}, sandbox, CONTAINER_ID,
                context={"session_id": SESSION_ID},
            )

        assert "error" in result
        assert "500 byte cap" in result["error"]

    @pytest.mark.asyncio
    async def test_dedup_returns_existing_row(self):
        """INSERT loses the (session, sha256) race → return the existing row."""
        existing_uid = uuid.uuid4()
        existing = {"id": existing_uid, "filename": "first.txt", "size_bytes": 3}
        sandbox = _make_sandbox()
        store = MagicMock()
        store.write = AsyncMock(return_value=("aa/bb/" + "a" * 32, "ab" * 32, 3))

        with (
            patch("app.tools.fetch_one", new_callable=AsyncMock) as mock_fetch,
            patch("app.tools.get_file_store", return_value=store),
            patch("app.tools.get_max_bytes", return_value=1_000_000),
        ):
            mock_fetch.side_effect = [None, existing]
            result = await execute_builtin_tool(
                "save_artifact",
                {"path": "/tmp/second.txt", "name": "second.txt"},
                sandbox,
                CONTAINER_ID,
                context={"session_id": SESSION_ID},
            )

        assert result["file_id"] == str(existing_uid)
        assert result["filename"] == "first.txt"
        assert result["deduplicated"] is True
        # First call is the INSERT, second the SELECT of the existing row.
        assert "INSERT INTO files" in mock_fetch.await_args_list[0].args[0]
        assert "SELECT id, filename, size_bytes" in mock_fetch.await_args_list[1].args[0]

    @pytest.mark.asyncio
    async def test_explicit_name_wins(self):
        file_uid = uuid.uuid4()
        sandbox = _make_sandbox()
        store = MagicMock()
        store.write = AsyncMock(return_value=("aa/bb/" + "a" * 32, "ab" * 32, 4))

        with (
            patch("app.tools.fetch_one", new_callable=AsyncMock) as mock_fetch,
            patch("app.tools.get_file_store", return_value=store),
            patch("app.tools.get_max_bytes", return_value=1_000_000),
        ):
            mock_fetch.return_value = {"id": file_uid}
            result = await execute_builtin_tool(
                "save_artifact",
                {"path": "/tmp/data/out.bin", "name": "renamed.bin"},
                sandbox,
                CONTAINER_ID,
                context={"session_id": SESSION_ID},
            )

        assert result["filename"] == "renamed.bin"


# ---------------------------------------------------------------------------
# GET /v1/artifacts (list + metadata + content)
# ---------------------------------------------------------------------------


@patch("app.routes.artifacts.fetch_all", new_callable=AsyncMock)
def test_list_artifacts_filters_source_artifact(mock_fetch_all, client):
    mock_fetch_all.return_value = [_make_file_row()]

    resp = client.get("/v1/artifacts", headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["source"] == "artifact"
    sql = mock_fetch_all.await_args.args[0]
    assert "source = 'artifact'" in sql
    assert "project_id = $1" in sql
    assert str(mock_fetch_all.await_args.args[1]) == "00000000-0000-0000-0000-000000000001"


@patch("app.routes.artifacts.fetch_all", new_callable=AsyncMock)
def test_list_artifacts_session_filter(mock_fetch_all, client):
    mock_fetch_all.return_value = [_make_file_row()]

    resp = client.get(f"/v1/artifacts?session_id={SESSION_ID}", headers=AUTH)

    assert resp.status_code == 200
    sql = mock_fetch_all.await_args.args[0]
    assert "scope_id = $2" in sql
    assert "scope_type = 'session'" in sql
    assert str(mock_fetch_all.await_args.args[2]) == SESSION_ID


def test_list_artifacts_rejects_bad_session_id(client):
    resp = client.get("/v1/artifacts?session_id=not-a-uuid", headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_error"


@patch("app.routes.artifacts.fetch_all", new_callable=AsyncMock)
def test_list_artifacts_pagination(mock_fetch_all, client):
    mock_fetch_all.return_value = [_make_file_row(filename=f"a{i}") for i in range(3)]

    resp = client.get("/v1/artifacts?limit=2", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True


@patch("app.routes.artifacts.fetch_one", new_callable=AsyncMock)
def test_get_artifact_metadata(mock_fetch, client):
    file_id = str(uuid.uuid4())
    mock_fetch.return_value = _make_file_row(file_id=file_id)

    resp = client.get(f"/v1/artifacts/{file_id}", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == file_id
    assert body["source"] == "artifact"
    assert body["downloadable"] is True
    sql = mock_fetch.await_args.args[0]
    assert "source = 'artifact'" in sql
    assert "archived_at IS NULL" in sql


@patch("app.routes.artifacts.fetch_one", new_callable=AsyncMock)
def test_get_artifact_404_when_not_artifact(mock_fetch, client):
    # Row exists but as an upload — the artifact view must not expose it.
    mock_fetch.return_value = None

    resp = client.get(f"/v1/artifacts/{uuid.uuid4()}", headers=AUTH)

    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


@patch("app.routes.artifacts.fetch_one", new_callable=AsyncMock)
@patch("app.routes.artifacts.get_file_store")
def test_get_artifact_content_streams(mock_get_store, mock_fetch, client):
    mock_fetch.return_value = _make_file_row(filename="result.csv")
    store = MagicMock()
    store.read = MagicMock(return_value=iter([b"a,b\n", b"1,2\n"]))
    mock_get_store.return_value = store

    resp = client.get(f"/v1/artifacts/{uuid.uuid4()}/content", headers=AUTH)

    assert resp.status_code == 200
    assert resp.content == b"a,b\n1,2\n"
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "result.csv" in resp.headers["Content-Disposition"]


@patch("app.routes.artifacts.fetch_one", new_callable=AsyncMock)
def test_get_artifact_content_404(mock_fetch, client):
    mock_fetch.return_value = None

    resp = client.get(f"/v1/artifacts/{uuid.uuid4()}/content", headers=AUTH)

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/sessions/{id}/filesystem (+ /raw)
# ---------------------------------------------------------------------------


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_filesystem_list_returns_entries(mock_fetch, client):
    mock_fetch.return_value = _make_session_row()
    sandbox = client.app.state.sandbox
    sandbox.list_dir = AsyncMock(return_value=[
        {
            "name": "outputs", "type": "dir", "size": 4096,
            "permissions": "drwxr-xr-x", "owner": "root", "group": "root",
            "modified": "2026-05-13 10:00", "link_target": None,
        },
        {
            "name": "result.csv", "type": "file", "size": 14,
            "permissions": "-rw-r--r--", "owner": "root", "group": "root",
            "modified": "2026-05-13 10:01", "link_target": None,
        },
    ])

    resp = client.get(f"/v1/sessions/{SESSION_ID}/filesystem?path=/mnt/session", headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "/mnt/session"
    assert len(body["entries"]) == 2
    assert body["entries"][1]["name"] == "result.csv"
    sandbox.list_dir.assert_awaited_once_with(CONTAINER_ID, "/mnt/session")


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_filesystem_list_404_unknown_session(mock_fetch, client):
    mock_fetch.return_value = None

    resp = client.get(f"/v1/sessions/{SESSION_ID}/filesystem?path=/", headers=AUTH)

    assert resp.status_code == 404


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_filesystem_list_404_bad_uuid(mock_fetch, client):
    resp = client.get("/v1/sessions/not-a-uuid/filesystem?path=/", headers=AUTH)
    assert resp.status_code == 404


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_filesystem_list_409_without_container(mock_fetch, client):
    mock_fetch.return_value = _make_session_row(container_id=None)

    resp = client.get(f"/v1/sessions/{SESSION_ID}/filesystem?path=/", headers=AUTH)

    assert resp.status_code == 409
    assert resp.json()["error"] == "no_container"


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_filesystem_list_422_relative_path(mock_fetch, client):
    mock_fetch.return_value = _make_session_row()

    resp = client.get(f"/v1/sessions/{SESSION_ID}/filesystem?path=rel/path", headers=AUTH)

    assert resp.status_code == 422


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_filesystem_list_maps_sandbox_error_to_400(mock_fetch, client):
    mock_fetch.return_value = _make_session_row()
    sandbox = client.app.state.sandbox
    sandbox.list_dir = AsyncMock(side_effect=SandboxError("Cannot list '/nope': ls failed"))

    resp = client.get(f"/v1/sessions/{SESSION_ID}/filesystem?path=/nope", headers=AUTH)

    assert resp.status_code == 400
    assert resp.json()["error"] == "filesystem_error"


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_filesystem_raw_downloads_bytes(mock_fetch, client):
    mock_fetch.return_value = _make_session_row()
    sandbox = client.app.state.sandbox
    sandbox.copy_from_container = AsyncMock(return_value=b"raw bytes here")

    resp = client.get(
        f"/v1/sessions/{SESSION_ID}/filesystem/raw?path=/tmp/report.txt", headers=AUTH
    )

    assert resp.status_code == 200, resp.text
    assert resp.content == b"raw bytes here"
    assert resp.headers["Content-Type"].startswith("text/plain")
    assert "report.txt" in resp.headers["Content-Disposition"]
    sandbox.copy_from_container.assert_awaited_once_with(CONTAINER_ID, "/tmp/report.txt")


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_filesystem_raw_maps_sandbox_error_to_400(mock_fetch, client):
    mock_fetch.return_value = _make_session_row()
    sandbox = client.app.state.sandbox
    sandbox.copy_from_container = AsyncMock(
        side_effect=SandboxError("Cannot read '/x': not a regular file")
    )

    resp = client.get(f"/v1/sessions/{SESSION_ID}/filesystem/raw?path=/x", headers=AUTH)

    assert resp.status_code == 400
    assert resp.json()["error"] == "filesystem_error"


@patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
def test_filesystem_raw_requires_path(mock_fetch, client):
    mock_fetch.return_value = _make_session_row()

    resp = client.get(f"/v1/sessions/{SESSION_ID}/filesystem/raw", headers=AUTH)

    assert resp.status_code == 422
