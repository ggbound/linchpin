"""Atlas B7 — deployments (scheduled agent runs, FR-8.1).

Covers: cron/timezone validation + next-run computation (timezone-aware),
CRUD + pause/resume HTTP surface, the FOR UPDATE SKIP LOCKED claim query,
and the scheduler trigger path (session creation via create_session,
deployment_id stamping, initial_events seeding, next_run_at recompute).
The DB layer is fully mocked.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import DEFAULT_ORG_ID, DEFAULT_PROJECT_ID
from app.deployments import (
    claim_due_deployments,
    compute_next_run,
    deployments_scheduler_loop,
    scheduler_enabled,
    trigger_deployment,
    validate_cron,
    validate_initial_events,
    validate_timezone,
)

AUTH = {"Authorization": "Bearer test-secret-key"}

AGENT_UUID = "11111111-1111-1111-1111-111111111111"
ENV_UUID = "22222222-2222-2222-2222-222222222222"
DEP_UUID = "33333333-3333-3333-3333-333333333333"


def _deployment_row(**overrides):
    """Fake asyncpg Record-like dict for a deployments row."""
    row = {
        "id": uuid.UUID(DEP_UUID),
        "project_id": uuid.UUID(DEFAULT_PROJECT_ID),
        "agent_id": uuid.UUID(AGENT_UUID),
        "environment_id": uuid.UUID(ENV_UUID),
        "cron": "*/5 * * * *",
        "timezone": "UTC",
        "initial_events": [{"type": "user.message", "payload": {"content": "go"}}],
        "status": "active",
        "next_run_at": datetime(2026, 10, 20, 12, 5, tzinfo=dt_timezone.utc),
        "created_at": datetime(2026, 10, 20, 11, 0, tzinfo=dt_timezone.utc),
        "archived_at": None,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# cron / timezone validation + compute_next_run
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validate_cron_accepts_standard_expressions(self):
        validate_cron("*/5 * * * *")
        validate_cron("0 9 * * 1-5")
        validate_cron("30 4 1,15 * *")

    def test_validate_cron_rejects_garbage(self):
        with pytest.raises(ValueError):
            validate_cron("not a cron")
        with pytest.raises(ValueError):
            validate_cron("61 * * * *")

    def test_validate_timezone_rejects_unknown_zone(self):
        validate_timezone("UTC")
        validate_timezone("Asia/Shanghai")
        with pytest.raises(ValueError):
            validate_timezone("Mars/Olympus_Mons")

    def test_compute_next_run_advances_by_interval(self):
        base = datetime(2026, 10, 20, 12, 3, tzinfo=dt_timezone.utc)
        nxt = compute_next_run("*/5 * * * *", "UTC", after=base)
        assert nxt == datetime(2026, 10, 20, 12, 5, tzinfo=dt_timezone.utc)

    def test_compute_next_run_is_timezone_aware(self):
        # 08:00 UTC = 16:00 Shanghai; the next "0 9 * * *" match is the
        # *local* 9am tomorrow (2026-10-21 01:00 UTC), not 09:00 UTC today.
        base = datetime(2026, 10, 20, 8, 0, tzinfo=dt_timezone.utc)
        shanghai = compute_next_run("0 9 * * *", "Asia/Shanghai", after=base)
        utc = compute_next_run("0 9 * * *", "UTC", after=base)
        assert utc == datetime(2026, 10, 20, 9, 0, tzinfo=dt_timezone.utc)
        assert shanghai == datetime(2026, 10, 21, 1, 0, tzinfo=dt_timezone.utc)
        # Returned values are UTC-normalized for storage.
        assert shanghai.tzinfo is dt_timezone.utc

    def test_compute_next_run_rejects_bad_cron(self):
        with pytest.raises(ValueError):
            compute_next_run("nope", "UTC")

    def test_validate_initial_events_rejects_forged_types(self):
        validate_initial_events([{"type": "user.message", "payload": {}}])
        with pytest.raises(ValueError):
            validate_initial_events([{"type": "agent.message", "payload": {}}])
        with pytest.raises(ValueError):
            validate_initial_events([{"type": "session.status", "payload": {}}])
        with pytest.raises(ValueError):
            validate_initial_events(["not a dict"])


# ---------------------------------------------------------------------------
# CRUD + pause/resume HTTP surface
# ---------------------------------------------------------------------------


class TestDeploymentCRUD:
    def test_create_returns_active_deployment(self, client: TestClient):
        body = {
            "agent_id": AGENT_UUID,
            "environment_id": ENV_UUID,
            "cron": "0 9 * * 1-5",
            "timezone": "Asia/Shanghai",
            "initial_events": [{"type": "user.message", "payload": {"content": "run"}}],
        }
        with patch(
            "app.routes.deployments.fetch_one", new_callable=AsyncMock
        ) as mock_fetch, patch(
            "app.routes.deployments.record_audit", new_callable=AsyncMock
        ):
            mock_fetch.side_effect = [
                {"id": uuid.UUID(AGENT_UUID)},                       # agent lookup
                {"id": uuid.UUID(ENV_UUID)},                          # environment lookup
                _deployment_row(cron="0 9 * * 1-5", timezone="Asia/Shanghai"),  # INSERT RETURNING
            ]
            res = client.post("/v1/deployments", json=body, headers=AUTH)

        assert res.status_code == 201, res.text
        dep = res.json()
        assert dep["status"] == "active"
        assert dep["cron"] == "0 9 * * 1-5"
        assert dep["timezone"] == "Asia/Shanghai"
        assert dep["archived_at"] is None
        assert dep["next_run_at"] is not None

        # C1 — every lookup is project-scoped.
        agent_sql, agent_args = (
            mock_fetch.await_args_list[0].args[0],
            mock_fetch.await_args_list[0].args[1:],
        )
        assert "project_id = $2" in agent_sql
        assert agent_args[1] == uuid.UUID(DEFAULT_PROJECT_ID)

    def test_create_rejects_invalid_cron(self, client: TestClient):
        body = {
            "agent_id": AGENT_UUID,
            "environment_id": ENV_UUID,
            "cron": "definitely not cron",
        }
        with patch("app.routes.deployments.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [
                {"id": uuid.UUID(AGENT_UUID)},
                {"id": uuid.UUID(ENV_UUID)},
            ]
            res = client.post("/v1/deployments", json=body, headers=AUTH)

        assert res.status_code == 422
        assert res.json()["error"] == "validation_error"

    def test_create_rejects_invalid_timezone(self, client: TestClient):
        body = {
            "agent_id": AGENT_UUID,
            "environment_id": ENV_UUID,
            "cron": "0 9 * * *",
            "timezone": "Nowhere/Nothing",
        }
        with patch("app.routes.deployments.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [
                {"id": uuid.UUID(AGENT_UUID)},
                {"id": uuid.UUID(ENV_UUID)},
            ]
            res = client.post("/v1/deployments", json=body, headers=AUTH)

        assert res.status_code == 422

    def test_create_rejects_foreign_agent(self, client: TestClient):
        body = {"agent_id": AGENT_UUID, "environment_id": ENV_UUID, "cron": "0 9 * * *"}
        with patch("app.routes.deployments.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [None]  # agent not in caller's project
            res = client.post("/v1/deployments", json=body, headers=AUTH)

        assert res.status_code == 404

    def test_list_is_project_scoped(self, client: TestClient):
        with patch(
            "app.routes.deployments.fetch_all", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = [[_deployment_row()]]
            res = client.get("/v1/deployments", headers=AUTH)

        assert res.status_code == 200
        body = res.json()
        assert body["has_more"] is False
        assert body["data"][0]["id"] == DEP_UUID
        sql, args = mock_fetch.await_args_list[0].args[0], mock_fetch.await_args_list[0].args[1:]
        assert "project_id = $1" in sql
        assert args[0] == uuid.UUID(DEFAULT_PROJECT_ID)

    def test_get_unknown_returns_404(self, client: TestClient):
        with patch(
            "app.routes.deployments.fetch_one", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = [None]
            res = client.get(f"/v1/deployments/{DEP_UUID}", headers=AUTH)

        assert res.status_code == 404

    def test_get_invalid_uuid_returns_404(self, client: TestClient):
        res = client.get("/v1/deployments/not-a-uuid", headers=AUTH)
        assert res.status_code == 404

    def test_patch_cron_recomputes_next_run(self, client: TestClient):
        with patch(
            "app.routes.deployments.fetch_one", new_callable=AsyncMock
        ) as mock_fetch, patch(
            "app.routes.deployments.execute", new_callable=AsyncMock
        ) as mock_exec, patch(
            "app.routes.deployments.record_audit", new_callable=AsyncMock
        ):
            updated = _deployment_row(cron="0 10 * * *")
            mock_fetch.side_effect = [
                _deployment_row(),       # ownership check
                {"cron": "*/5 * * * *", "timezone": "UTC"},  # current (cron given, tz filled)
                updated,                 # final read
            ]
            res = client.patch(
                f"/v1/deployments/{DEP_UUID}",
                json={"cron": "0 10 * * *"},
                headers=AUTH,
            )

        assert res.status_code == 200, res.text
        assert res.json()["cron"] == "0 10 * * *"
        # The next_run_at UPDATE must have fired with a recomputed value.
        next_run_updates = [
            c for c in mock_exec.await_args_list
            if "next_run_at = $2" in c.args[0]
        ]
        assert len(next_run_updates) == 1
        # execute(sql, deployment_uid, next_run_at)
        assert next_run_updates[0].args[1] == uuid.UUID(DEP_UUID)
        assert isinstance(next_run_updates[0].args[2], datetime)

    def test_patch_rejects_invalid_cron(self, client: TestClient):
        with patch(
            "app.routes.deployments.fetch_one", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = [
                _deployment_row(),                            # ownership check
                {"cron": "*/5 * * * *", "timezone": "UTC"},   # current (tz filled)
            ]
            res = client.patch(
                f"/v1/deployments/{DEP_UUID}",
                json={"cron": "bogus"},
                headers=AUTH,
            )

        assert res.status_code == 422

    def test_delete_archives_softly(self, client: TestClient):
        with patch(
            "app.routes.deployments.fetch_one", new_callable=AsyncMock
        ) as mock_fetch, patch(
            "app.routes.deployments.execute", new_callable=AsyncMock
        ) as mock_exec, patch(
            "app.routes.deployments.record_audit", new_callable=AsyncMock
        ):
            mock_fetch.side_effect = [
                _deployment_row(),
                _deployment_row(status="paused"),
            ]
            res = client.delete(f"/v1/deployments/{DEP_UUID}", headers=AUTH)

        assert res.status_code == 200
        assert res.json()["status"] == "paused"
        sql = mock_exec.await_args_list[0].args[0]
        assert "archived_at = NOW()" in sql
        assert "status = 'paused'" in sql

    def test_pause_sets_status(self, client: TestClient):
        with patch(
            "app.routes.deployments.fetch_one", new_callable=AsyncMock
        ) as mock_fetch, patch(
            "app.routes.deployments.execute", new_callable=AsyncMock
        ) as mock_exec, patch(
            "app.routes.deployments.record_audit", new_callable=AsyncMock
        ):
            mock_fetch.side_effect = [
                _deployment_row(),
                _deployment_row(status="paused"),
            ]
            res = client.post(f"/v1/deployments/{DEP_UUID}/pause", headers=AUTH)

        assert res.status_code == 200
        assert res.json()["status"] == "paused"
        sql, args = mock_exec.await_args.args[0], mock_exec.await_args.args[1:]
        assert "status = 'paused'" in sql
        assert args == (uuid.UUID(DEP_UUID),)

    def test_resume_recomputes_next_run(self, client: TestClient):
        with patch(
            "app.routes.deployments.fetch_one", new_callable=AsyncMock
        ) as mock_fetch, patch(
            "app.routes.deployments.execute", new_callable=AsyncMock
        ) as mock_exec, patch(
            "app.routes.deployments.record_audit", new_callable=AsyncMock
        ):
            mock_fetch.side_effect = [
                _deployment_row(status="paused"),
                _deployment_row(),
            ]
            res = client.post(f"/v1/deployments/{DEP_UUID}/resume", headers=AUTH)

        assert res.status_code == 200, res.text
        assert res.json()["status"] == "active"
        sql = mock_exec.await_args.args[0]
        assert "status = 'active'" in sql and "next_run_at = $2" in sql


# ---------------------------------------------------------------------------
# Scheduler: claim + trigger + loop
# ---------------------------------------------------------------------------


def _due_dep():
    return {
        "id": uuid.UUID(DEP_UUID),
        "project_id": uuid.UUID(DEFAULT_PROJECT_ID),
        "agent_id": uuid.UUID(AGENT_UUID),
        "environment_id": uuid.UUID(ENV_UUID),
        "cron": "*/5 * * * *",
        "timezone": "UTC",
        "initial_events": [{"type": "user.message", "payload": {"content": "go"}}],
        "due_at": datetime(2026, 10, 20, 12, 5, tzinfo=dt_timezone.utc),
    }


class TestClaimDueDeployments:
    @pytest.mark.asyncio
    async def test_claim_uses_skip_locked_and_returns_due_at(self):
        with patch("app.deployments.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [[_due_dep()]]
            rows = await claim_due_deployments()

        assert len(rows) == 1
        assert rows[0]["due_at"].minute == 5
        sql, args = mock_fetch.await_args.args[0], mock_fetch.await_args.args[1:]
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert "status = 'active'" in sql
        assert "archived_at IS NULL" in sql
        assert "next_run_at <= NOW()" in sql
        assert args == (10,)

    @pytest.mark.asyncio
    async def test_claim_empty_returns_empty_list(self):
        with patch("app.deployments.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [[]]
            assert await claim_due_deployments() == []


class TestTriggerDeployment:
    @pytest.mark.asyncio
    async def test_trigger_creates_session_and_recomputes(self):
        fake_session = SimpleNamespace(id="44444444-4444-4444-4444-444444444444")
        with patch(
            "app.deployments.fetch_one", new_callable=AsyncMock
        ) as mock_fetch, patch(
            "app.deployments.execute", new_callable=AsyncMock
        ) as mock_exec, patch(
            "app.routes.sessions.create_session", new_callable=AsyncMock
        ) as mock_create, patch(
            "app.orchestrator.append_event", new_callable=AsyncMock
        ) as mock_append:
            mock_fetch.side_effect = [
                {"project_id": uuid.UUID(DEFAULT_PROJECT_ID),
                 "org_id": uuid.UUID(DEFAULT_ORG_ID)},
            ]
            mock_create.side_effect = [fake_session]

            result = await trigger_deployment(_due_dep(), app=SimpleNamespace())

        assert result == "triggered"
        mock_create.assert_awaited_once()

        # The synthetic body carries agent + environment + deployment metadata.
        body = mock_create.await_args.args[0]
        assert body.agent_id == AGENT_UUID
        assert body.environment_id == ENV_UUID
        assert body.metadata == {"deployment_id": DEP_UUID}
        # FieldInfo landmines — internal direct calls must bypass both.
        assert mock_create.await_args.kwargs["idempotency_key"] is None
        assert mock_create.await_args.kwargs["events_encrypted"] is False

        # Session stamped with the deployment id.
        stamp_calls = [
            c for c in mock_exec.await_args_list
            if "SET deployment_id" in c.args[0]
        ]
        assert len(stamp_calls) == 1

        # initial_events seeded as user.message.
        mock_append.assert_awaited_once()
        assert mock_append.await_args.args[1] == "user.message"
        assert mock_append.await_args.args[2] == {"content": "go"}

        # next_run_at recomputed from the *scheduled* due time (12:05 → 12:10).
        recompute = [
            c for c in mock_exec.await_args_list
            if "SET next_run_at" in c.args[0]
        ]
        assert len(recompute) == 1
        assert recompute[0].args[1] == datetime(
            2026, 10, 20, 12, 10, tzinfo=dt_timezone.utc
        )

    @pytest.mark.asyncio
    async def test_trigger_with_vanished_agent_only_reschedules(self):
        with patch(
            "app.deployments.fetch_one", new_callable=AsyncMock
        ) as mock_fetch, patch(
            "app.deployments.execute", new_callable=AsyncMock
        ) as mock_exec, patch(
            "app.routes.sessions.create_session", new_callable=AsyncMock
        ) as mock_create:
            mock_fetch.side_effect = [None]  # agent gone

            result = await trigger_deployment(_due_dep(), app=SimpleNamespace())

        assert result == "agent_missing"
        mock_create.assert_not_awaited()
        recompute = [
            c for c in mock_exec.await_args_list
            if "SET next_run_at" in c.args[0]
        ]
        assert len(recompute) == 1

    @pytest.mark.asyncio
    async def test_trigger_survives_create_failure(self):
        with patch(
            "app.deployments.fetch_one", new_callable=AsyncMock
        ) as mock_fetch, patch(
            "app.deployments.execute", new_callable=AsyncMock
        ), patch(
            "app.routes.sessions.create_session", new_callable=AsyncMock
        ) as mock_create:
            mock_fetch.side_effect = [
                {"project_id": uuid.UUID(DEFAULT_PROJECT_ID),
                 "org_id": uuid.UUID(DEFAULT_ORG_ID)},
            ]
            mock_create.side_effect = [RuntimeError("sandbox down")]

            result = await trigger_deployment(_due_dep(), app=SimpleNamespace())

        assert result == "error"  # rescheduled, not crashed


class TestSchedulerLoop:
    @pytest.mark.asyncio
    async def test_loop_triggers_due_deployments(self):
        with patch(
            "app.deployments.claim_due_deployments", new_callable=AsyncMock
        ) as mock_claim, patch(
            "app.deployments.trigger_deployment", new_callable=AsyncMock
        ) as mock_trigger:
            mock_claim.side_effect = [[_due_dep()], asyncio.CancelledError()]
            mock_trigger.side_effect = ["triggered"]

            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(
                    deployments_scheduler_loop(app=SimpleNamespace(), poll_interval=0),
                    timeout=1,
                )

        mock_trigger.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_loop_survives_claim_crash(self):
        # A claim crash must be swallowed (logged) so the loop keeps
        # ticking — the second tick then hits the CancelledError exit.
        with patch(
            "app.deployments.claim_due_deployments", new_callable=AsyncMock
        ) as mock_claim, patch(
            "app.deployments.trigger_deployment", new_callable=AsyncMock
        ) as mock_trigger:
            mock_claim.side_effect = [RuntimeError("db gone"), asyncio.CancelledError()]

            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(
                    deployments_scheduler_loop(app=SimpleNamespace(), poll_interval=0),
                    timeout=1,
                )

        mock_trigger.assert_not_awaited()

    def test_scheduler_enabled_env_gate(self, monkeypatch):
        monkeypatch.setenv("LINCHPIN_DEPLOYMENTS_SCHEDULER", "false")
        assert scheduler_enabled() is False
        monkeypatch.setenv("LINCHPIN_DEPLOYMENTS_SCHEDULER", "true")
        assert scheduler_enabled() is True
        monkeypatch.delenv("LINCHPIN_DEPLOYMENTS_SCHEDULER")
        assert scheduler_enabled() is True
