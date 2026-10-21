"""Atlas B10.1 — GET /v1/usage aggregation tests.

Covers: day/project grouping, from/to window filters, empty result
shaping, and auth enforcement. The DB layer is fully mocked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.auth import DEFAULT_PROJECT_ID

AUTH = {"Authorization": "Bearer test-secret-key"}


def _usage_row(bucket, sessions, inp, out, cc, cr):
    """Fake asyncpg Record-like dict for an aggregate row."""
    return {
        "bucket": bucket,
        "sessions": sessions,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
    }


def test_usage_defaults_to_day_grouping(client: TestClient):
    with patch(
        "app.routes.usage.fetch_all", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = [
            [_usage_row("2026-10-19", 2, 100, 50, 10, 5)],
            [_usage_row("total", 2, 100, 50, 10, 5)],
        ]
        res = client.get("/v1/usage", headers=AUTH)

    assert res.status_code == 200
    body = res.json()
    assert body["group_by"] == "day"
    assert body["from"] is None and body["to"] is None
    assert body["total"]["sessions"] == 2
    assert body["total"]["input_tokens"] == 100
    assert body["buckets"][0]["bucket"] == "2026-10-19"
    assert body["buckets"][0]["cache_read_input_tokens"] == 5

    buckets_sql, buckets_args = (
        mock_fetch.await_args_list[0].args[0],
        mock_fetch.await_args_list[0].args[1:],
    )
    assert "date_trunc('day'" in buckets_sql
    assert "GROUP BY bucket" in buckets_sql
    assert "project_id = $1" in buckets_sql
    assert buckets_args == (uuid.UUID(DEFAULT_PROJECT_ID),)


def test_usage_from_to_window(client: TestClient):
    with patch(
        "app.routes.usage.fetch_all", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = [
            [_usage_row("2026-10-19", 1, 7, 3, 0, 0)],
            [_usage_row("total", 1, 7, 3, 0, 0)],
        ]
        res = client.get(
            "/v1/usage",
            params={"from": "2026-10-01T00:00:00Z", "to": "2026-10-19T23:59:59Z"},
            headers=AUTH,
        )

    assert res.status_code == 200
    body = res.json()
    assert body["from"] == "2026-10-01T00:00:00Z"
    assert body["to"] == "2026-10-19T23:59:59Z"

    sql, *args = mock_fetch.await_args_list[0].args
    assert "created_at >= $2" in sql
    assert "created_at <= $3" in sql
    assert args[0] == uuid.UUID(DEFAULT_PROJECT_ID)
    assert args[1] == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert args[2] == datetime(2026, 10, 19, 23, 59, 59, tzinfo=timezone.utc)


def test_usage_group_by_project(client: TestClient):
    with patch(
        "app.routes.usage.fetch_all", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = [
            [_usage_row(DEFAULT_PROJECT_ID, 9, 1, 2, 3, 4)],
            [_usage_row("total", 9, 1, 2, 3, 4)],
        ]
        res = client.get("/v1/usage", params={"group_by": "project"}, headers=AUTH)

    assert res.status_code == 200
    assert res.json()["group_by"] == "project"
    buckets_sql = mock_fetch.await_args_list[0].args[0]
    assert "project_id::text" in buckets_sql
    assert "date_trunc" not in buckets_sql


def test_usage_empty_result(client: TestClient):
    with patch(
        "app.routes.usage.fetch_all", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = [[], []]
        res = client.get("/v1/usage", headers=AUTH)

    assert res.status_code == 200
    body = res.json()
    assert body["buckets"] == []
    assert body["total"] == {
        "bucket": "total",
        "sessions": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


def test_usage_requires_auth(client: TestClient):
    res = client.get("/v1/usage")
    assert res.status_code == 401
