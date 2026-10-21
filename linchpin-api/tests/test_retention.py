"""Atlas B12 — events retention tests.

Covers the retention window configuration and the cleanup loop's
disabled / active behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.retention import (
    cleanup_expired_events,
    cleanup_loop,
    get_retention_days,
)


def test_default_retention_days(monkeypatch):
    monkeypatch.delenv("LINCHPIN_EVENTS_RETENTION_DAYS", raising=False)
    assert get_retention_days() == 90


def test_retention_days_env_override(monkeypatch):
    monkeypatch.setenv("LINCHPIN_EVENTS_RETENTION_DAYS", "30")
    assert get_retention_days() == 30


def test_retention_days_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("LINCHPIN_EVENTS_RETENTION_DAYS", "not-a-number")
    assert get_retention_days() == 90


@pytest.mark.parametrize("value", ["0", "-5"])
def test_retention_days_disabled(monkeypatch, value):
    monkeypatch.setenv("LINCHPIN_EVENTS_RETENTION_DAYS", value)
    assert get_retention_days() <= 0


@pytest.mark.asyncio
@patch("app.retention.execute", new_callable=AsyncMock)
async def test_cleanup_disabled_never_queries(mock_execute, monkeypatch):
    monkeypatch.setenv("LINCHPIN_EVENTS_RETENTION_DAYS", "0")
    await cleanup_expired_events()
    mock_execute.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.retention.execute", new_callable=AsyncMock, return_value="DELETE 7")
async def test_cleanup_deletes_by_age(mock_execute, monkeypatch):
    monkeypatch.setenv("LINCHPIN_EVENTS_RETENTION_DAYS", "90")
    await cleanup_expired_events()
    mock_execute.assert_awaited_once()
    sql, *args = mock_execute.await_args.args
    assert "DELETE FROM events" in sql
    assert "created_at < now()" in sql
    assert args == ["90"]


@pytest.mark.asyncio
async def test_cleanup_loop_disabled_returns(monkeypatch):
    """A disabled retention window must not enter the infinite loop."""
    monkeypatch.setenv("LINCHPIN_EVENTS_RETENTION_DAYS", "0")
    # If the loop entered, this await would never return; the call
    # returning at all is the assertion.
    await asyncio_wait_for_loop_start()
    assert True


async def asyncio_wait_for_loop_start():
    await cleanup_loop(interval_sec=0.0)
