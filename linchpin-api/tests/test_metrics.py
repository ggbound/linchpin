"""Atlas B10 — tests for the Prometheus /metrics endpoint and gauges."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# /metrics endpoint (no auth, root app)
# ---------------------------------------------------------------------------


def test_metrics_endpoint_public_and_complete(client):
    # No Authorization header — /metrics must be scrapeable without auth.
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]

    body = resp.text
    # Upgrade-plan gauges all present
    assert "linchpin_active_sessions" in body
    assert "linchpin_orchestrator_tasks" in body
    assert "linchpin_sandbox_containers" in body
    assert "linchpin_webhook_delivery_backlog" in body
    # Instrumentator HTTP metrics present
    assert "http_request_duration" in body


def test_health_and_metrics_excluded_from_http_metrics(client):
    client.get("/health")
    resp = client.get("/metrics")
    body = resp.text
    # excluded_handlers=["health", "metrics"] — their samples only show
    # the instrumentator's own scrape, not per-handler entries
    assert 'handler="/health"' not in body
    assert 'handler="/metrics"' not in body


# ---------------------------------------------------------------------------
# In-process gauge
# ---------------------------------------------------------------------------


def test_register_inprocess_sources_reflects_dict_size():
    from prometheus_client import generate_latest

    from app import metrics

    tasks = {"a": MagicMock(), "b": MagicMock()}
    metrics.register_inprocess_sources(tasks)
    # set_function computes at scrape time — sample the exposition to check.
    body = generate_latest().decode()
    assert "linchpin_orchestrator_tasks 2.0" in body


# ---------------------------------------------------------------------------
# Sampler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sample_gauges_sets_db_and_docker_gauges():
    from app import metrics

    rows = [
        {"n": 3},  # active sessions
        {"n": 5},  # webhook backlog
    ]

    async def mock_fetch_one(sql, *args):
        return rows.pop(0)

    with (
        patch("app.metrics.fetch_one", side_effect=mock_fetch_one),
        patch.object(metrics, "_count_sandbox_containers", return_value=2),
    ):
        await metrics.sample_gauges()

    assert metrics.ACTIVE_SESSIONS._value.get() == 3
    assert metrics.WEBHOOK_BACKLOG._value.get() == 5
    assert metrics.SANDBOX_CONTAINERS._value.get() == 2


@pytest.mark.asyncio
async def test_sample_gauges_best_effort_on_db_failure():
    from app import metrics

    with (
        patch("app.metrics.fetch_one", AsyncMock(side_effect=RuntimeError("no pool"))),
        patch.object(metrics, "_count_sandbox_containers", return_value=0),
    ):
        # Must not raise even with the DB pool uninitialised.
        await metrics.sample_gauges()


@pytest.mark.asyncio
async def test_sampler_loop_refreshes_periodically(monkeypatch):
    from app import metrics

    monkeypatch.setenv("LINCHPIN_METRICS_INTERVAL_SEC", "0.01")
    calls = {"n": 0}

    async def fake_sample():
        calls["n"] += 1

    with patch.object(metrics, "sample_gauges", fake_sample):
        task = asyncio.create_task(metrics.metrics_sampler_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_sampler_loop_disabled_when_interval_nonpositive(monkeypatch):
    from app import metrics

    monkeypatch.setenv("LINCHPIN_METRICS_INTERVAL_SEC", "0")
    # Returns immediately — the timeout guards against a hang.
    await asyncio.wait_for(metrics.metrics_sampler_loop(), timeout=1)


# ---------------------------------------------------------------------------
# Docker container count
# ---------------------------------------------------------------------------


def test_count_sandbox_containers_filters_linchpin_networks():
    from app import metrics

    class FakeContainer:
        def __init__(self, nets):
            self.attrs = {"NetworkSettings": {"Networks": nets}}

    class FakeContainers:
        def list(self, all=False):
            return [
                FakeContainer({"linchpin-open": {}, "bridge": {}}),
                FakeContainer({"bridge": {}}),
                FakeContainer({"linchpin-none": {}}),
            ]

    class FakeClient:
        containers = FakeContainers()

    class FakeDocker:
        @staticmethod
        def from_env():
            return FakeClient()

    with patch.dict("sys.modules", {"docker": FakeDocker}):
        assert metrics._count_sandbox_containers() == 2


def test_count_sandbox_containers_degrades_to_zero_on_docker_error():
    from app import metrics

    class FakeDocker:
        @staticmethod
        def from_env():
            raise RuntimeError("no docker daemon")

    with patch.dict("sys.modules", {"docker": FakeDocker}):
        assert metrics._count_sandbox_containers() == 0
