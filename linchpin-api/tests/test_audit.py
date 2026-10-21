"""Atlas B9 — audit log tests.

Covers:
- ``record_audit`` best-effort semantics (never raises), actor derivation
  (apikey:<id> vs bootstrap) and project attribution.
- ``GET /v1/audit`` NDJSON export: filters, limit, keyset pagination,
  detail str/dict tolerance.
- Fail-open integration: a failing audit write must not break API routes.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.audit import record_audit
from app.auth import DEFAULT_PROJECT_ID, AuthContext

AUTH = {"Authorization": "Bearer test-secret-key"}

PROJECT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"


def _make_audit_row(
    *,
    action: str = "agent.created",
    resource_type: str = "agent",
    resource_id: str | None = None,
    actor: str = "bootstrap",
    project_id: str | None = PROJECT_A,
    detail: dict | str | None = None,
):
    """Build a fake asyncpg Record-like dict for an audit_log row."""
    return {
        "id": uuid.uuid4(),
        "at": datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        "project_id": uuid.UUID(project_id) if project_id else None,
        "actor": actor,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "detail": detail if detail is not None else {},
    }


# ---- record_audit unit tests ----


@pytest.mark.asyncio
@patch("app.audit.execute", new_callable=AsyncMock)
async def test_record_audit_apikey_actor(mock_execute):
    """A DB-issued key context yields actor 'apikey:<id>' and project from ctx."""
    ctx = AuthContext(
        project_id=PROJECT_A,
        org_id=PROJECT_A,
        api_key_id="11111111-1111-1111-1111-111111111111",
        scopes=("*",),
    )
    await record_audit(
        "agent.created", "agent", "abc", ctx=ctx, detail={"name": "x"}
    )

    mock_execute.assert_awaited_once()
    args = mock_execute.await_args.args
    # (query, project_id, actor, action, resource_type, resource_id, detail_json)
    assert args[1] == uuid.UUID(PROJECT_A)
    assert args[2] == "apikey:11111111-1111-1111-1111-111111111111"
    assert args[3] == "agent.created"
    assert args[4] == "agent"
    assert args[5] == "abc"
    assert json.loads(args[6]) == {"name": "x"}


@pytest.mark.asyncio
@patch("app.audit.execute", new_callable=AsyncMock)
async def test_record_audit_bootstrap_actor(mock_execute):
    """No ctx (or a bootstrap key without api_key_id) yields actor 'bootstrap'."""
    await record_audit("credential.resolved", "credential", detail={"v": 1})
    assert mock_execute.await_args.args[2] == "bootstrap"
    assert mock_execute.await_args.args[1] is None

    bootstrap_ctx = AuthContext(
        project_id=PROJECT_A, org_id=PROJECT_A, api_key_id=None, scopes=("*",)
    )
    await record_audit("x.y", "z", ctx=bootstrap_ctx)
    assert mock_execute.await_args_list[1].args[2] == "bootstrap"


@pytest.mark.asyncio
@patch("app.audit.execute", new_callable=AsyncMock)
async def test_record_audit_explicit_project_overrides(mock_execute):
    """An explicit project_id wins over ctx.project_id."""
    ctx = AuthContext(
        project_id=PROJECT_A, org_id=PROJECT_A, api_key_id=None, scopes=("*",)
    )
    other = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2"
    await record_audit("x.y", "z", ctx=ctx, project_id=other)
    assert mock_execute.await_args.args[1] == uuid.UUID(other)


@pytest.mark.asyncio
@patch(
    "app.audit.execute",
    new_callable=AsyncMock,
    side_effect=RuntimeError("pool not initialized"),
)
async def test_record_audit_never_raises(mock_execute, caplog):
    """Audit is fail-open: DB errors are logged, never propagated."""
    with caplog.at_level("WARNING", logger="app.audit"):
        await record_audit("x.y", "z", detail={"a": 1})
    mock_execute.assert_awaited_once()


# ---- GET /v1/audit export tests ----


@patch("app.routes.audit.fetch_all", new_callable=AsyncMock)
def test_export_streams_ndjson(mock_fetch, client: TestClient):
    """Rows come back as newline-delimited JSON, newest first, full shape."""
    mock_fetch.return_value = [
        _make_audit_row(resource_id="r1", detail={"name": "a"}),
        _make_audit_row(action="vault.deleted", resource_type="vault", detail=None),
    ]

    resp = client.get("/v1/audit", headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    lines = resp.text.strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["action"] == "agent.created"
    assert rec["resource_type"] == "agent"
    assert rec["resource_id"] == "r1"
    assert rec["actor"] == "bootstrap"
    assert rec["project_id"] == PROJECT_A
    assert rec["detail"] == {"name": "a"}
    assert rec["at"] == "2025-06-01T12:00:00+00:00"
    # second row: NULL detail serializes as {}
    assert json.loads(lines[1])["detail"] == {}


@patch("app.routes.audit.fetch_all", new_callable=AsyncMock)
def test_export_detail_json_string_parsed(mock_fetch, client: TestClient):
    """JSONB decoded as str (no codec) is parsed back into an object."""
    mock_fetch.return_value = [_make_audit_row(detail='{"k": "v"}')]

    resp = client.get("/v1/audit", headers=AUTH)
    assert resp.status_code == 200
    assert json.loads(resp.text.strip())["detail"] == {"k": "v"}


@patch("app.routes.audit.fetch_all", new_callable=AsyncMock)
def test_export_empty(mock_fetch, client: TestClient):
    """No rows → 200 with an empty NDJSON body."""
    mock_fetch.return_value = []
    resp = client.get("/v1/audit", headers=AUTH)
    assert resp.status_code == 200
    assert resp.text == ""
    mock_fetch.assert_awaited_once()


@patch("app.routes.audit.fetch_all", new_callable=AsyncMock)
def test_export_filters_in_query(mock_fetch, client: TestClient):
    """from/to/resource_type/action filters are bound into the SQL."""
    mock_fetch.return_value = []

    resp = client.get(
        "/v1/audit",
        params={
            "from": "2025-01-01T00:00:00Z",
            "to": "2025-02-01T00:00:00Z",
            "resource_type": "agent",
            "action": "agent.created",
            "limit": 10,
        },
        headers=AUTH,
    )
    assert resp.status_code == 200

    sql, *args = mock_fetch.await_args.args
    assert "project_id = $1" in sql
    assert "at >= $2" in sql
    assert "at <= $3" in sql
    assert "resource_type = $4" in sql
    assert "action = $5" in sql
    assert "LIMIT 10" in sql
    assert "ORDER BY at DESC, id DESC" in sql
    assert args[0] == uuid.UUID(DEFAULT_PROJECT_ID)
    assert args[1].isoformat().startswith("2025-01-01")
    assert args[4] == "agent.created"


@patch("app.routes.audit.fetch_all", new_callable=AsyncMock)
def test_export_rejects_oversized_limit(mock_fetch, client: TestClient):
    """limit above the 50000 hard cap is a validation error."""
    resp = client.get("/v1/audit", params={"limit": 50001}, headers=AUTH)
    assert resp.status_code == 422
    mock_fetch.assert_not_awaited()


@patch("app.routes.audit.fetch_all", new_callable=AsyncMock)
def test_export_keyset_pagination(mock_fetch, client: TestClient):
    """A full first batch triggers a second keyset-paged query."""
    first = [_make_audit_row() for _ in range(1000)]
    second = [_make_audit_row()]
    mock_fetch.side_effect = [first, second]

    resp = client.get("/v1/audit", params={"limit": 2000}, headers=AUTH)
    assert resp.status_code == 200
    lines = resp.text.strip().splitlines()
    assert len(lines) == 1001
    assert mock_fetch.await_count == 2

    second_sql, *second_args = mock_fetch.await_args_list[1].args
    assert "(at, id) < ($2, $3)" in second_sql
    assert second_args[1] == first[-1]["at"]
    assert second_args[2] == first[-1]["id"]
    assert "LIMIT 1000" in second_sql


# ---- fail-open integration ----


@patch("app.audit.execute", new_callable=AsyncMock, side_effect=RuntimeError("no db"))
@patch("app.routes.agents.fetch_one", new_callable=AsyncMock)
def test_failing_audit_write_does_not_break_routes(
    mock_fetch, _mock_audit_execute, client: TestClient
):
    """An audit write failure must not turn a successful route into a 500."""
    from tests.test_agents import VALID_PAYLOAD, _make_agent_row

    mock_fetch.return_value = _make_agent_row(name="my-agent")
    resp = client.post("/v1/agents", json=VALID_PAYLOAD, headers=AUTH)
    assert resp.status_code == 201
