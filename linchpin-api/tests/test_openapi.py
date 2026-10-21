"""Atlas M5 (FR-12.2) — OpenAPI 3.1 customization.

``custom_openapi`` post-processes the generated schema to publish bearer
security, the unified error envelope, tag metadata, and standardized
401/429/500 responses. These tests fetch ``GET /openapi.json`` from the
real app (schema generation is pure computation — no lifespan needed).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import _OPENAPI_NODE_AUTH_PATHS

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


@pytest.fixture(scope="module")
def schema():
    from app.main import app

    return TestClient(app).get("/openapi.json").json()


def test_openapi_3_schema(schema):
    assert schema["openapi"].startswith("3.1")


def test_bearer_security_scheme(schema):
    scheme = schema["components"]["securitySchemes"]["bearerAuth"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"


def test_error_envelope_schema_published(schema):
    envelope = schema["components"]["schemas"]["ErrorEnvelope"]
    assert {"error", "message", "detail"} <= set(envelope["properties"])
    assert set(envelope["required"]) == {"error", "message"}


def test_reusable_error_responses_registered(schema):
    assert {
        "Unauthorized", "RateLimited", "QuotaExceeded", "Internal",
    } <= set(schema["components"]["responses"])


def test_error_responses_reference_the_envelope(schema):
    for name in ("Unauthorized", "RateLimited", "QuotaExceeded", "Internal"):
        ref = schema["components"]["responses"][name]["content"][
            "application/json"
        ]["schema"]["$ref"]
        assert ref == "#/components/schemas/ErrorEnvelope", name


def test_all_v1_operations_declare_bearer_auth_and_errors(schema):
    for path, item in schema["paths"].items():
        if not path.startswith("/v1/") or path in _OPENAPI_NODE_AUTH_PATHS:
            continue
        for method, op in item.items():
            if method not in _HTTP_METHODS:
                continue
            where = f"{method.upper()} {path}"
            assert op.get("security") == [{"bearerAuth": []}], where
            assert "401" in op["responses"], where
            assert "429" in op["responses"], where
            assert "500" in op["responses"], where


def test_node_heartbeat_excluded_from_bearer_auth(schema):
    """The daemon heartbeat authenticates with a node token, not a key."""
    path = "/v1/executor-nodes/{node_id}/heartbeat"
    assert path in schema["paths"]
    op = schema["paths"][path]["post"]
    assert "security" not in op
    assert "401" not in op["responses"]


def test_session_create_advertises_quota_exceeded(schema):
    op = schema["paths"]["/v1/sessions"]["post"]
    desc = op["responses"]["429"]["description"]
    assert "quota_exceeded" in desc
    ref = op["responses"]["429"]["content"]["application/json"]["schema"]["$ref"]
    assert ref == "#/components/schemas/ErrorEnvelope"


def test_thread_spawn_advertises_quota_exceeded(schema):
    op = schema["paths"]["/v1/sessions/{parent_id}/threads"]["post"]
    assert "quota_exceeded" in op["responses"]["429"]["description"]


def test_regular_operations_advertise_rate_limiting(schema):
    op = schema["paths"]["/v1/agents"]["post"]
    desc = op["responses"]["429"]["description"]
    assert "Retry-After" in desc
    assert "quota_exceeded" not in desc


def test_tag_metadata_published(schema):
    names = {t["name"] for t in schema["tags"]}
    assert {"admin", "agents", "sessions", "memory", "usage"} <= names


def test_non_v1_paths_left_unannotated(schema):
    op = schema["paths"]["/health"]["get"]
    assert "security" not in op
    assert "401" not in op["responses"]
