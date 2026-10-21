"""Atlas M5 (FR-11.3) — project quota enforcement.

``enforce_session_quotas`` runs against the quota module's *own*
``fetch_one`` binding; tests patch ``app.quotas.fetch_one`` and assert
both 429 shapes plus the fail-open degradation when the pool is down.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.quotas import enforce_session_quotas

PROJECT = "00000000-0000-0000-0000-000000000001"


def _project_row(max_concurrent=None, monthly_budget=None):
    return {
        "max_concurrent_sessions": max_concurrent,
        "monthly_token_budget": monthly_budget,
    }


@pytest.fixture()
def fetch():
    with patch("app.quotas.fetch_one", new_callable=AsyncMock) as mock:
        yield mock


# --- no-limit paths -------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_project_id_skips_check(fetch):
    await enforce_session_quotas("not-a-uuid")
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_project_allows_creation(fetch):
    fetch.return_value = None
    await enforce_session_quotas(str(uuid.uuid4()))
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_limits_queries_project_row_only(fetch):
    fetch.return_value = _project_row()
    await enforce_session_quotas(PROJECT)
    fetch.assert_awaited_once()


# --- concurrent-session quota ----------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_limit_blocks_at_cap(fetch):
    fetch.side_effect = [
        _project_row(max_concurrent=2),
        {"active": 2},
    ]
    with pytest.raises(HTTPException) as excinfo:
        await enforce_session_quotas(PROJECT)
    assert excinfo.value.status_code == 429
    detail = excinfo.value.detail
    assert detail["error"] == "quota_exceeded"
    assert detail["limit_type"] == "concurrent_sessions"
    assert detail["limit"] == 2
    assert detail["active"] == 2


@pytest.mark.asyncio
async def test_concurrent_limit_passes_below_cap(fetch):
    fetch.side_effect = [
        _project_row(max_concurrent=2),
        {"active": 1},
    ]
    await enforce_session_quotas(PROJECT)
    assert fetch.await_count == 2


# --- monthly token budget ---------------------------------------------------------


@pytest.mark.asyncio
async def test_token_budget_blocks_at_cap(fetch):
    fetch.side_effect = [
        _project_row(monthly_budget=1_000),
        {"tokens": 1_000},
    ]
    with pytest.raises(HTTPException) as excinfo:
        await enforce_session_quotas(PROJECT)
    assert excinfo.value.status_code == 429
    detail = excinfo.value.detail
    assert detail["error"] == "quota_exceeded"
    assert detail["limit_type"] == "monthly_tokens"
    assert detail["limit"] == 1_000
    assert detail["used"] == 1_000


@pytest.mark.asyncio
async def test_token_budget_passes_below_cap(fetch):
    fetch.side_effect = [
        _project_row(monthly_budget=1_000),
        {"tokens": 999},
    ]
    await enforce_session_quotas(PROJECT)
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_quota_fires_before_token_budget(fetch):
    fetch.side_effect = [
        _project_row(max_concurrent=1, monthly_budget=1),
        {"active": 1},
    ]
    with pytest.raises(HTTPException) as excinfo:
        await enforce_session_quotas(PROJECT)
    assert excinfo.value.detail["limit_type"] == "concurrent_sessions"
    assert fetch.await_count == 2  # token query never runs


# --- fail-open degradation (pool not initialised / DB down) -----------------------


@pytest.mark.asyncio
async def test_pool_down_fails_open_on_project_lookup(fetch):
    fetch.side_effect = RuntimeError("pool not initialised")
    await enforce_session_quotas(PROJECT)  # must not raise
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_pool_down_fails_open_on_count(fetch):
    fetch.side_effect = [
        _project_row(max_concurrent=5),
        RuntimeError("pool closed"),
    ]
    await enforce_session_quotas(PROJECT)
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_pool_down_fails_open_on_usage(fetch):
    fetch.side_effect = [
        _project_row(monthly_budget=5),
        RuntimeError("pool closed"),
    ]
    await enforce_session_quotas(PROJECT)
    assert fetch.await_count == 2
