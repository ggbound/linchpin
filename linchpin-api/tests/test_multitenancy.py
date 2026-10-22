"""Atlas C1 — multi-tenancy tests: issued-key auth, scopes, and the
admin surface (``/v1/projects`` + ``/v1/api-keys``).

Bootstrap-key behavior (``LINCHPIN_API_KEY``) is covered in
``test_auth.py``; here we exercise the DB-issued key path with mocked
``app.auth`` / ``app.routes.admin`` DB symbols.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import AuthContext, require_auth, verify_bearer_token


DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _clear_auth_cache():
    """Isolate tests from the module-level 60s auth cache."""
    from app import auth as auth_mod

    auth_mod._auth_cache.clear()
    yield
    auth_mod._auth_cache.clear()


@pytest.fixture()
def auth_client(monkeypatch: pytest.MonkeyPatch):
    """App with read+write routes that surface the resolved AuthContext."""
    monkeypatch.setenv("LINCHPIN_API_KEY", "test-secret-key")

    app = FastAPI()
    router = APIRouter(prefix="/v1", dependencies=[Depends(verify_bearer_token)])

    @router.get("/ping")
    async def ping(ctx: AuthContext = Depends(require_auth)):
        return {
            "project_id": ctx.project_id,
            "org_id": ctx.org_id,
            "api_key_id": ctx.api_key_id,
            "scopes": list(ctx.scopes),
        }

    @router.post("/ping")
    async def post_ping(ctx: AuthContext = Depends(require_auth)):
        return {"ok": True}

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _api_key_row(
    *, token: str, scopes=("agents:write",), status="active",
    project_id=DEFAULT_TENANT, org_id=DEFAULT_TENANT,
    rate_limit_rpm=None,
):
    key_id = str(uuid.uuid4())
    return {
        "api_key_id": uuid.UUID(key_id),
        "scopes": list(scopes),
        "status": status,
        "rate_limit_rpm": rate_limit_rpm,
        "project_id": uuid.UUID(project_id),
        "org_id": uuid.UUID(org_id),
    }, key_id


# --- issued-key authentication ---------------------------------------------


def test_issued_key_authenticates_and_injects_context(auth_client):
    token = "lpk_" + uuid.uuid4().hex
    row, key_id = _api_key_row(token=token, scopes=("agents:write",))

    with (
        patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=row),
        patch("app.auth.execute", new_callable=AsyncMock) as mock_exec,
    ):
        resp = auth_client.get("/v1/ping", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == DEFAULT_TENANT
    assert body["org_id"] == DEFAULT_TENANT
    assert body["api_key_id"] == key_id
    assert body["scopes"] == ["agents:write"]
    # last_used_at touchpoint fired.
    mock_exec.assert_awaited_once()


def test_unknown_issued_key_returns_401(auth_client):
    token = "lpk_" + uuid.uuid4().hex
    with patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=None):
        resp = auth_client.get("/v1/ping", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "unauthorized"


def test_revoked_key_returns_401(auth_client):
    token = "lpk_" + uuid.uuid4().hex
    row, _ = _api_key_row(token=token, status="revoked")
    with patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=row):
        resp = auth_client.get("/v1/ping", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_issued_key_accepted_via_query_token(auth_client):
    """SSE/EventSource compat: ``?token=`` works for DB-issued keys too."""
    token = "lpk_" + uuid.uuid4().hex
    row, _ = _api_key_row(token=token)
    with (
        patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=row),
        patch("app.auth.execute", new_callable=AsyncMock),
    ):
        resp = auth_client.get("/v1/ping", params={"token": token})
    assert resp.status_code == 200


def test_issued_key_cached_within_ttl(auth_client):
    """A second request inside the 60s TTL must not re-query the DB."""
    token = "lpk_" + uuid.uuid4().hex
    row, _ = _api_key_row(token=token)
    with (
        patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=row) as mock_fetch,
        patch("app.auth.execute", new_callable=AsyncMock),
    ):
        r1 = auth_client.get("/v1/ping", headers={"Authorization": f"Bearer {token}"})
        r2 = auth_client.get("/v1/ping", headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == r2.status_code == 200
    assert mock_fetch.await_count == 1


# --- scope enforcement -------------------------------------------------------


def test_get_allowed_for_any_valid_key(auth_client):
    """Scopes only gate mutations — GET is readable with any active key."""
    token = "lpk_" + uuid.uuid4().hex
    row, _ = _api_key_row(token=token, scopes=("vaults:write",))
    with (
        patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=row),
        patch("app.auth.execute", new_callable=AsyncMock),
    ):
        resp = auth_client.get("/v1/ping", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_write_without_matching_scope_returns_403(auth_client):
    token = "lpk_" + uuid.uuid4().hex
    row, _ = _api_key_row(token=token, scopes=("agents:write",))
    with (
        patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=row),
        patch("app.auth.execute", new_callable=AsyncMock),
    ):
        resp = auth_client.post("/v1/ping", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    body = resp.json()["detail"]
    assert body["error"] == "forbidden"
    assert body["message"] == "missing scope ping:write"


def test_write_with_matching_scope_passes(auth_client):
    token = "lpk_" + uuid.uuid4().hex
    row, _ = _api_key_row(token=token, scopes=("ping:write",))
    with (
        patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=row),
        patch("app.auth.execute", new_callable=AsyncMock),
    ):
        resp = auth_client.post("/v1/ping", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_wildcard_scope_allows_writes(auth_client):
    token = "lpk_" + uuid.uuid4().hex
    row, _ = _api_key_row(token=token, scopes=("*",))
    with (
        patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=row),
        patch("app.auth.execute", new_callable=AsyncMock),
    ):
        resp = auth_client.post("/v1/ping", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


# --- admin surface (real app, mocked DB) -------------------------------------


def _project_row(project_id=DEFAULT_TENANT, org_id=DEFAULT_TENANT, name="Default Project"):
    return {
        "id": uuid.UUID(project_id),
        "org_id": uuid.UUID(org_id),
        "name": name,
        "slug": "default",
        "max_concurrent_sessions": None,
        "monthly_token_budget": None,
        "created_at": datetime.now(tz=timezone.utc),
    }


def _apikey_db_row(
    *, project_id=DEFAULT_TENANT, name="ci", prefix="lpk_abcd1234",
    scopes=("agents:write",), status="active", rate_limit_rpm=None,
):
    return {
        "id": uuid.uuid4(),
        "project_id": uuid.UUID(project_id),
        "name": name,
        "prefix": prefix,
        "scopes": list(scopes),
        "status": status,
        "rate_limit_rpm": rate_limit_rpm,
        "created_at": datetime.now(tz=timezone.utc),
        "last_used_at": None,
    }


def test_list_projects(client):
    with patch(
        "app.routes.admin.fetch_all", new_callable=AsyncMock,
        return_value=[_project_row()],
    ):
        resp = client.get("/v1/projects", headers={"Authorization": "Bearer test-secret-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == DEFAULT_TENANT
    assert body[0]["slug"] == "default"


def test_create_api_key_returns_plaintext_once(client):
    now = datetime.now(tz=timezone.utc)
    # Mirror the route's default: no scopes in the body → ["*"] is stored.
    inserted = _apikey_db_row(scopes=("*",))
    inserted["created_at"] = now

    with patch(
        "app.routes.admin.fetch_one", new_callable=AsyncMock,
        side_effect=[
            {"org_id": uuid.UUID(DEFAULT_TENANT)},  # project ownership check
            inserted,  # INSERT ... RETURNING
        ],
    ):
        resp = client.post(
            "/v1/api-keys",
            json={"name": "ci"},
            headers={"Authorization": "Bearer test-secret-key"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["key"].startswith("lpk_")
    # The static mock row can't echo the freshly generated prefix; the
    # prefix/plaintext consistency is asserted in the sha256 test below.
    assert len(body["prefix"]) == 12
    assert body["scopes"] == ["*"]
    assert body["status"] == "active"
    assert "key_hash" not in body
    assert body["name"] == "ci"


def test_create_api_key_persists_sha256_of_returned_key(client):
    captured = {}

    async def _fetch_one_side(sql, *args):
        if "FROM projects" in sql:
            return {"org_id": uuid.UUID(DEFAULT_TENANT)}
        # INSERT ... RETURNING — capture the key_hash argument.
        captured["project_id"] = args[0]
        captured["name"] = args[1]
        captured["key_hash"] = args[2]
        captured["prefix"] = args[3]
        captured["scopes"] = args[4]
        row = _apikey_db_row(name=args[1], prefix=args[3], scopes=tuple(args[4]))
        return row

    with patch(
        "app.routes.admin.fetch_one", new_callable=AsyncMock,
        side_effect=_fetch_one_side,
    ):
        resp = client.post(
            "/v1/api-keys",
            json={"name": "ci", "scopes": ["agents:write"]},
            headers={"Authorization": "Bearer test-secret-key"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert captured["key_hash"] == hashlib.sha256(body["key"].encode()).hexdigest()
    assert captured["prefix"] == body["key"][:12]
    assert captured["scopes"] == ["agents:write"]
    assert str(captured["project_id"]) == DEFAULT_TENANT


def test_create_api_key_rejects_other_org_project(client):
    other_project = str(uuid.uuid4())
    with patch(
        "app.routes.admin.fetch_one", new_callable=AsyncMock,
        return_value={"org_id": uuid.uuid4()},  # belongs to another org
    ):
        resp = client.post(
            "/v1/api-keys",
            json={"name": "x", "project_id": other_project},
            headers={"Authorization": "Bearer test-secret-key"},
        )
    assert resp.status_code == 404


def test_create_api_key_unknown_project_404(client):
    with patch(
        "app.routes.admin.fetch_one", new_callable=AsyncMock, return_value=None
    ):
        resp = client.post(
            "/v1/api-keys",
            json={"name": "x", "project_id": str(uuid.uuid4())},
            headers={"Authorization": "Bearer test-secret-key"},
        )
    assert resp.status_code == 404


def test_create_api_key_cross_project_drops_tenant_guc(client):
    """Bootstrap provisioning a fresh project (the POST /v1/projects
    flow): the api_keys INSERT must run with the tenant GUC dropped
    (RLS fail-open, same semantics as pre-auth queries) and be restored
    to the caller's project right after."""
    other_project = str(uuid.uuid4())
    guc_calls = []

    async def _fetch_one_side(sql, *args):
        if "FROM projects" in sql:
            return {"org_id": uuid.UUID(DEFAULT_TENANT)}  # ownership check
        return _apikey_db_row()  # INSERT ... RETURNING

    with (
        patch("app.routes.admin.fetch_one", new_callable=AsyncMock, side_effect=_fetch_one_side),
        patch("app.routes.admin.set_tenant_context", side_effect=lambda v: guc_calls.append(v)),
        patch("app.routes.admin.record_audit", new_callable=AsyncMock),
    ):
        resp = client.post(
            "/v1/api-keys",
            json={"name": "u", "project_id": other_project},
            headers={"Authorization": "Bearer test-secret-key"},
        )

    assert resp.status_code == 201
    assert [(str(v) if v else v) for v in guc_calls] == [None, DEFAULT_TENANT]


def test_create_api_key_same_project_keeps_tenant_guc(client):
    """Same-project issuance takes the RLS-safe path — no GUC juggling."""
    guc_calls = []

    async def _fetch_one_side(sql, *args):
        if "FROM projects" in sql:
            return {"org_id": uuid.UUID(DEFAULT_TENANT)}
        return _apikey_db_row()

    with (
        patch("app.routes.admin.fetch_one", new_callable=AsyncMock, side_effect=_fetch_one_side),
        patch("app.routes.admin.set_tenant_context", side_effect=lambda v: guc_calls.append(v)),
        patch("app.routes.admin.record_audit", new_callable=AsyncMock),
    ):
        resp = client.post(
            "/v1/api-keys",
            json={"name": "u"},
            headers={"Authorization": "Bearer test-secret-key"},
        )

    assert resp.status_code == 201
    assert guc_calls == []


def test_list_api_keys_never_returns_secret_material(client):
    with patch(
        "app.routes.admin.fetch_all", new_callable=AsyncMock,
        return_value=[_apikey_db_row()],
    ):
        resp = client.get("/v1/api-keys", headers={"Authorization": "Bearer test-secret-key"})
    assert resp.status_code == 200
    for item in resp.json():
        assert "key" not in item
        assert "key_hash" not in item
        assert item["prefix"].startswith("lpk_")


def test_revoke_api_key(client):
    with patch(
        "app.routes.admin.execute", new_callable=AsyncMock, return_value="UPDATE 1"
    ):
        resp = client.delete(
            f"/v1/api-keys/{uuid.uuid4()}",
            headers={"Authorization": "Bearer test-secret-key"},
        )
    assert resp.status_code == 204


def test_revoke_api_key_unknown_or_other_project_404(client):
    with patch(
        "app.routes.admin.execute", new_callable=AsyncMock, return_value="UPDATE 0"
    ):
        resp = client.delete(
            f"/v1/api-keys/{uuid.uuid4()}",
            headers={"Authorization": "Bearer test-secret-key"},
        )
    assert resp.status_code == 404


def test_revoke_api_key_malformed_id_404(client):
    resp = client.delete(
        "/v1/api-keys/not-a-uuid",
        headers={"Authorization": "Bearer test-secret-key"},
    )
    assert resp.status_code == 404


# --- project creation (Atlas C1, POST /v1/projects) ---------------------------


def test_create_project_issued_key_forbidden(client):
    """Project creation is bootstrap-only (same rule as PATCH quotas) —
    an issued key gets 403 even with full scopes."""
    token = "lpk_" + uuid.uuid4().hex
    row, _ = _api_key_row(token=token, scopes=("*",))
    with (
        patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=row),
        patch("app.auth.execute", new_callable=AsyncMock),
    ):
        resp = client.post(
            "/v1/projects",
            json={"name": "User 1024"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "forbidden"
    assert "bootstrap" in body["message"]


def test_create_project_derives_slug_and_audits(client):
    captured = {}

    async def _fetch_one_side(sql, *args):
        captured["sql"] = " ".join(sql.split())
        captured["args"] = list(args)
        row = _project_row()
        row["id"] = uuid.uuid4()
        row["name"] = args[1]
        row["slug"] = args[2]
        return row

    with (
        patch("app.routes.admin.fetch_one", new_callable=AsyncMock, side_effect=_fetch_one_side),
        patch("app.routes.admin.record_audit", new_callable=AsyncMock) as audit,
    ):
        resp = client.post(
            "/v1/projects",
            json={"name": "User 1024 Workspace"},
            headers={"Authorization": "Bearer test-secret-key"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "user-1024-workspace"
    assert body["max_concurrent_sessions"] is None
    assert body["monthly_token_budget"] is None
    assert "INSERT INTO projects" in captured["sql"]
    # Bootstrap key's org (the default tenant) owns the new project.
    assert str(captured["args"][0]) == DEFAULT_TENANT
    assert captured["args"][1] == "User 1024 Workspace"
    audit.assert_awaited_once()
    assert audit.await_args.args[0] == "project.created"
    assert audit.await_args.args[2] == body["id"]


def test_create_project_explicit_slug_and_quotas(client):
    async def _fetch_one_side(sql, *args):
        row = _project_row()
        row["slug"] = args[2]
        row["max_concurrent_sessions"] = args[3]
        row["monthly_token_budget"] = args[4]
        return row

    with (
        patch("app.routes.admin.fetch_one", new_callable=AsyncMock, side_effect=_fetch_one_side),
        patch("app.routes.admin.record_audit", new_callable=AsyncMock),
    ):
        resp = client.post(
            "/v1/projects",
            json={
                "name": "Pro",
                "slug": "pro-tenant",
                "max_concurrent_sessions": 4,
                "monthly_token_budget": 1_000_000,
            },
            headers={"Authorization": "Bearer test-secret-key"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "pro-tenant"
    assert body["max_concurrent_sessions"] == 4
    assert body["monthly_token_budget"] == 1_000_000


def test_create_project_cjk_name_falls_back_to_random_slug(client):
    async def _fetch_one_side(sql, *args):
        row = _project_row()
        row["name"] = args[1]
        row["slug"] = args[2]
        return row

    with (
        patch("app.routes.admin.fetch_one", new_callable=AsyncMock, side_effect=_fetch_one_side),
        patch("app.routes.admin.record_audit", new_callable=AsyncMock),
    ):
        resp = client.post(
            "/v1/projects",
            json={"name": "用户专属项目"},
            headers={"Authorization": "Bearer test-secret-key"},
        )

    assert resp.status_code == 201
    # Random-hex fallback: 12 chars, kebab-safe, no empty slug.
    assert len(resp.json()["slug"]) == 12


def test_create_project_slug_conflict_409(client):
    with patch(
        "app.routes.admin.fetch_one", new_callable=AsyncMock,
        side_effect=asyncpg.UniqueViolationError("duplicate key value"),
    ):
        resp = client.post(
            "/v1/projects",
            json={"name": "Default", "slug": "default"},
            headers={"Authorization": "Bearer test-secret-key"},
        )
    assert resp.status_code == 409
    body = resp.json()  # unified handler flattens the envelope to top level
    assert body["error"] == "conflict"
    assert "default" in body["message"]


def test_create_project_invalid_slug_422(client):
    resp = client.post(
        "/v1/projects",
        json={"name": "X", "slug": "Not Valid!"},
        headers={"Authorization": "Bearer test-secret-key"},
    )
    assert resp.status_code == 422


def test_create_project_empty_name_422(client):
    resp = client.post(
        "/v1/projects",
        json={"name": ""},
        headers={"Authorization": "Bearer test-secret-key"},
    )
    assert resp.status_code == 422


# --- project quota management (Atlas M5, PATCH /v1/projects/{id}) -------------


def test_patch_project_issued_key_forbidden(client):
    """Quota management is bootstrap-only: an issued key gets 403 even
    with full scopes — a tenant key must not lift its own limits."""
    token = "lpk_" + uuid.uuid4().hex
    row, _ = _api_key_row(token=token, scopes=("*",))
    with (
        patch("app.auth.fetch_one", new_callable=AsyncMock, return_value=row),
        patch("app.auth.execute", new_callable=AsyncMock),
    ):
        resp = client.patch(
            f"/v1/projects/{DEFAULT_TENANT}",
            json={"max_concurrent_sessions": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "forbidden"
    assert "bootstrap" in body["message"]


def test_patch_project_sets_both_quotas(client):
    captured = {}

    async def _fetch_one_side(sql, *args):
        captured["sql"] = " ".join(sql.split())
        captured["args"] = list(args)
        row = _project_row()
        row["max_concurrent_sessions"] = 10
        row["monthly_token_budget"] = 5_000_000
        return row

    with (
        patch("app.routes.admin.fetch_one", new_callable=AsyncMock, side_effect=_fetch_one_side),
        patch("app.routes.admin.record_audit", new_callable=AsyncMock) as audit,
    ):
        resp = client.patch(
            f"/v1/projects/{DEFAULT_TENANT}",
            json={"max_concurrent_sessions": 10, "monthly_token_budget": 5000000},
            headers={"Authorization": "Bearer test-secret-key"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["max_concurrent_sessions"] == 10
    assert body["monthly_token_budget"] == 5000000
    assert "UPDATE projects" in captured["sql"]
    assert "max_concurrent_sessions = $1" in captured["sql"]
    assert "monthly_token_budget = $2" in captured["sql"]
    # org_id is appended as an extra WHERE guard on the tenant column.
    assert "org_id = $3" in captured["sql"]
    assert captured["args"][:2] == [10, 5_000_000]
    assert str(captured["args"][2]) == DEFAULT_TENANT
    audit.assert_awaited_once()
    assert audit.await_args.args[0] == "project.quota_updated"


def test_patch_project_explicit_null_clears_quota(client):
    captured = {}

    async def _fetch_one_side(sql, *args):
        captured["sql"] = " ".join(sql.split())
        captured["args"] = list(args)
        return _project_row()  # cleared → None

    with (
        patch("app.routes.admin.fetch_one", new_callable=AsyncMock, side_effect=_fetch_one_side),
        patch("app.routes.admin.record_audit", new_callable=AsyncMock),
    ):
        resp = client.patch(
            f"/v1/projects/{DEFAULT_TENANT}",
            json={"max_concurrent_sessions": None},
            headers={"Authorization": "Bearer test-secret-key"},
        )

    assert resp.status_code == 200
    assert resp.json()["max_concurrent_sessions"] is None
    # Only the named column is touched; org guard + id follow.
    assert "monthly_token_budget" not in captured["sql"]
    assert captured["args"][0] is None


def test_patch_project_empty_body_is_read_only_select(client):
    """Omitted fields keep current values — no UPDATE, no audit event."""
    with (
        patch("app.routes.admin.fetch_one", new_callable=AsyncMock, return_value=_project_row()) as mock_fetch,
        patch("app.routes.admin.record_audit", new_callable=AsyncMock) as audit,
    ):
        resp = client.patch(
            f"/v1/projects/{DEFAULT_TENANT}",
            json={},
            headers={"Authorization": "Bearer test-secret-key"},
        )

    assert resp.status_code == 200
    sql = " ".join(mock_fetch.await_args.args[0].split())
    assert sql.startswith("SELECT")
    # SELECT path: org_id guard appended after the id.
    assert str(mock_fetch.await_args.args[2]) == DEFAULT_TENANT
    audit.assert_not_awaited()


def test_patch_project_unknown_or_other_org_404(client):
    with patch("app.routes.admin.fetch_one", new_callable=AsyncMock, return_value=None):
        resp = client.patch(
            f"/v1/projects/{uuid.uuid4()}",
            json={"max_concurrent_sessions": 5},
            headers={"Authorization": "Bearer test-secret-key"},
        )
    assert resp.status_code == 404


def test_patch_project_malformed_id_404(client):
    resp = client.patch(
        "/v1/projects/not-a-uuid",
        json={"max_concurrent_sessions": 5},
        headers={"Authorization": "Bearer test-secret-key"},
    )
    assert resp.status_code == 404
