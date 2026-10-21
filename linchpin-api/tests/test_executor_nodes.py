"""Atlas M4 (FR-4.6) — executor node registry tests.

Covers the /v1/executor-nodes CRUD surface (token returned exactly once,
Fernet + hash + prefix storage, org scoping, soft delete) and the
daemon self-auth heartbeat. Bootstrap-key auth is used throughout.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from asyncpg.exceptions import UniqueViolationError

from app.auth import DEFAULT_ORG_ID
from app.routes.executor_nodes import TOKEN_PREFIX_TAG, _node_status


AUTH = {"Authorization": "Bearer test-secret-key"}

_NODE_ID = uuid.uuid4()


class _FakeEncryption:
    """Deterministic stand-in for the Fernet service."""

    def encrypt(self, value: str) -> str:
        return "enc:" + value[::-1]

    def decrypt(self, value: str) -> str:
        return value[len("enc"):][::-1]


@pytest.fixture(autouse=True)
def _clear_auth_cache():
    """Isolate tests from the module-level 60s auth cache."""
    from app import auth as auth_mod

    auth_mod._auth_cache.clear()
    yield
    auth_mod._auth_cache.clear()


def _node_row(**overrides):
    """Full executor_nodes row (the _NODE_COLUMNS shape)."""
    row = {
        "id": _NODE_ID,
        "org_id": uuid.UUID(DEFAULT_ORG_ID),
        "name": "gpu-box-1",
        "url": "http://10.0.0.8:8100",
        "token_encrypted": "enc:tok",
        "token_hash": "deadbeef",
        "token_prefix": "lex_AAAAAAAA",
        "max_sessions": 5,
        "last_heartbeat_at": None,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# POST /v1/executor-nodes — registration
# ---------------------------------------------------------------------------


def test_register_node_returns_token_once(client):
    with (
        patch("app.routes.executor_nodes.fetch_one", new_callable=AsyncMock) as mock_fetch,
        patch("app.routes.executor_nodes.record_audit", new_callable=AsyncMock),
        patch("app.encryption.get_encryption_service", return_value=_FakeEncryption()),
    ):
        inserted = {}

        async def _capture_insert(sql, *args):
            inserted["sql"] = sql
            inserted["args"] = args
            return {
                "id": _NODE_ID,
                "org_id": uuid.UUID(DEFAULT_ORG_ID),
                "name": "gpu-box-1",
                "url": "http://10.0.0.8:8100",
                "token_prefix": args[5],
                "max_sessions": args[6],
                "last_heartbeat_at": None,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }

        mock_fetch.side_effect = _capture_insert

        resp = client.post(
            "/v1/executor-nodes",
            json={"name": "gpu-box-1", "url": "http://10.0.0.8:8100"},
            headers=AUTH,
        )

    assert resp.status_code == 201
    body = resp.json()

    token = body["token"]
    assert token.startswith(TOKEN_PREFIX_TAG)
    # lex_ + 32 bytes of url-safe entropy (43 chars).
    assert len(token) == 4 + 43

    # Storage layout: Fernet-encrypted token + SHA-256 hash + 12-char prefix.
    args = inserted["args"]
    assert args[0] == uuid.UUID(DEFAULT_ORG_ID)
    assert args[1] == "gpu-box-1"
    assert args[2] == "http://10.0.0.8:8100"
    assert args[3] == "enc:" + token[::-1]
    assert args[4] == hashlib.sha256(token.encode()).hexdigest()
    assert args[5] == token[:12]
    assert args[6] == 5  # default max_sessions

    assert body["prefix"] == token[:12]
    assert body["name"] == "gpu-box-1"
    assert body["status"] == "offline"  # never heartbeated


def test_register_node_encryption_unavailable_returns_503(client):
    with (
        patch(
            "app.encryption.get_encryption_service",
            side_effect=RuntimeError("VAULT_ENCRYPTION_KEY is not configured"),
        ),
        patch("app.routes.executor_nodes.fetch_one", new_callable=AsyncMock) as mock_fetch,
    ):
        resp = client.post(
            "/v1/executor-nodes",
            json={"name": "gpu-box-1", "url": "http://10.0.0.8:8100"},
            headers=AUTH,
        )

    assert resp.status_code == 503
    assert resp.json()["error"] == "encryption_unavailable"
    mock_fetch.assert_not_awaited()


def test_register_node_name_conflict_returns_409(client):
    with (
        patch(
            "app.routes.executor_nodes.fetch_one",
            new_callable=AsyncMock,
            side_effect=UniqueViolationError("duplicate key value violates "
                                             "unique constraint"),
        ),
        patch("app.routes.executor_nodes.record_audit", new_callable=AsyncMock),
        patch("app.encryption.get_encryption_service", return_value=_FakeEncryption()),
    ):
        resp = client.post(
            "/v1/executor-nodes",
            json={"name": "gpu-box-1", "url": "http://10.0.0.8:8100"},
            headers=AUTH,
        )

    assert resp.status_code == 409
    assert resp.json()["error"] == "name_conflict"


# ---------------------------------------------------------------------------
# GET /v1/executor-nodes — list
# ---------------------------------------------------------------------------


def test_list_nodes_reports_status_and_org_scope(client):
    rows = [
        _node_row(name="fresh-node",
                  last_heartbeat_at=datetime.now(timezone.utc)),
        _node_row(name="stale-node",
                  last_heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=120)),
        _node_row(name="never-node", last_heartbeat_at=None),
    ]
    with (
        patch("app.routes.executor_nodes.fetch_all", new_callable=AsyncMock, return_value=rows) as mock_fetch_all,
    ):
        resp = client.get("/v1/executor-nodes", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    statuses = {n["name"]: n["status"] for n in body}
    assert statuses == {
        "fresh-node": "online",
        "stale-node": "offline",
        "never-node": "offline",
    }
    # Secret material never leaves the API.
    for node in body:
        assert "token_encrypted" not in node
        assert "token_hash" not in node
        assert node["prefix"] == "lex_AAAAAAAA"
    # Org-scoped query.
    assert mock_fetch_all.await_args[0][1] == uuid.UUID(DEFAULT_ORG_ID)


# ---------------------------------------------------------------------------
# GET / DELETE /v1/executor-nodes/{id} — detail + soft delete
# ---------------------------------------------------------------------------


def test_get_node_detail(client):
    with (
        patch("app.routes.executor_nodes.fetch_one", new_callable=AsyncMock, return_value=_node_row()),
    ):
        resp = client.get(f"/v1/executor-nodes/{_NODE_ID}", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "gpu-box-1"
    assert body["url"] == "http://10.0.0.8:8100"
    assert "token_encrypted" not in body


def test_get_node_not_found(client):
    with (
        patch("app.routes.executor_nodes.fetch_one", new_callable=AsyncMock, return_value=None),
    ):
        resp = client.get(f"/v1/executor-nodes/{_NODE_ID}", headers=AUTH)

    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


def test_get_node_invalid_uuid_skips_db(client):
    with (
        patch("app.routes.executor_nodes.fetch_one", new_callable=AsyncMock) as mock_fetch,
    ):
        resp = client.get("/v1/executor-nodes/not-a-uuid", headers=AUTH)

    assert resp.status_code == 404
    mock_fetch.assert_not_awaited()


def test_delete_node_soft_deletes(client):
    with (
        patch("app.routes.executor_nodes.execute", new_callable=AsyncMock, return_value="UPDATE 1") as mock_exec,
        patch("app.routes.executor_nodes.record_audit", new_callable=AsyncMock),
    ):
        resp = client.delete(f"/v1/executor-nodes/{_NODE_ID}", headers=AUTH)

    assert resp.status_code == 204
    sql = mock_exec.await_args[0][0]
    assert "archived_at = now()" in sql


def test_delete_node_unknown_returns_404(client):
    with (
        patch("app.routes.executor_nodes.execute", new_callable=AsyncMock, return_value="UPDATE 0"),
    ):
        resp = client.delete(f"/v1/executor-nodes/{_NODE_ID}", headers=AUTH)

    assert resp.status_code == 404


def test_delete_node_invalid_uuid_skips_db(client):
    with (
        patch("app.routes.executor_nodes.execute", new_callable=AsyncMock) as mock_exec,
    ):
        resp = client.delete("/v1/executor-nodes/not-a-uuid", headers=AUTH)

    assert resp.status_code == 404
    mock_exec.assert_not_awaited()


# ---------------------------------------------------------------------------
# POST /v1/executor-nodes/{id}/heartbeat — daemon self-auth
# ---------------------------------------------------------------------------


_DAEMON_TOKEN = "lex_mydaemon0123456789abcdef0123456789abcdef0123456"


def test_heartbeat_success(client):
    with (
        patch("app.routes.executor_nodes.fetch_one", new_callable=AsyncMock, return_value={"id": _NODE_ID}) as mock_fetch,
        patch("app.routes.executor_nodes.execute", new_callable=AsyncMock) as mock_exec,
    ):
        resp = client.post(
            f"/v1/executor-nodes/{_NODE_ID}/heartbeat",
            headers={"Authorization": f"Bearer {_DAEMON_TOKEN}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"id": str(_NODE_ID), "status": "online"}
    # Node looked up by id + token hash (self-auth, no API key involved).
    assert "executor_nodes" in mock_fetch.await_args[0][0]
    assert mock_fetch.await_args[0][1] == _NODE_ID
    assert mock_fetch.await_args[0][2] == hashlib.sha256(
        _DAEMON_TOKEN.encode()
    ).hexdigest()
    # Liveness refreshed.
    assert "last_heartbeat_at = now()" in mock_exec.await_args[0][0]


def test_heartbeat_wrong_token_returns_401(client):
    with (
        patch("app.routes.executor_nodes.fetch_one", new_callable=AsyncMock, return_value=None),
    ):
        resp = client.post(
            f"/v1/executor-nodes/{_NODE_ID}/heartbeat",
            headers={"Authorization": "Bearer lex_wrong_token"},
        )

    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


def test_heartbeat_missing_token_returns_401(client):
    resp = client.post(f"/v1/executor-nodes/{_NODE_ID}/heartbeat")
    assert resp.status_code == 401


def test_heartbeat_invalid_uuid_returns_404(client):
    with (
        patch("app.routes.executor_nodes.fetch_one", new_callable=AsyncMock) as mock_fetch,
    ):
        resp = client.post(
            "/v1/executor-nodes/not-a-uuid/heartbeat",
            headers={"Authorization": f"Bearer {_DAEMON_TOKEN}"},
        )

    assert resp.status_code == 404
    mock_fetch.assert_not_awaited()


# ---------------------------------------------------------------------------
# _node_status unit tests
# ---------------------------------------------------------------------------


class TestNodeStatus:
    def test_none_is_offline(self):
        assert _node_status(None) == "offline"

    def test_recent_heartbeat_is_online(self):
        assert _node_status(datetime.now(timezone.utc)) == "online"

    def test_stale_heartbeat_is_offline(self):
        stale = datetime.now(timezone.utc) - timedelta(seconds=120)
        assert _node_status(stale) == "offline"

    def test_freshness_window_boundary(self):
        fresh = datetime.now(timezone.utc) - timedelta(seconds=89)
        expired = datetime.now(timezone.utc) - timedelta(seconds=91)
        assert _node_status(fresh) == "online"
        assert _node_status(expired) == "offline"
