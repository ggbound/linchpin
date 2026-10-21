"""Atlas C2 — unit tests for cross-replica cluster primitives.

Covers the four pieces that make the API tier horizontally scalable:

- instance identity (``get_instance_id`` / ``set_instance_id``)
- advisory-lock key mapping and per-session channel round-trips
- ``SessionLease`` semantics (acquire/renew/release/broken) and the
  orchestrator wiring around it (``run_session`` wrapper, heartbeat,
  broken-lease exit)
- ``EventBus`` demultiplexing + ``app.db.listen``/``notify`` shared-channel
  convergence (bus mode vs. fallback mode)
- the lease supervisor (``take_over_stale_sessions`` takeover branches)

All tests run without a DB pool: ``get_pool`` raises ``RuntimeError`` and
the cluster primitives must degrade to pre-C2 single-process semantics.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import cluster, db, orchestrator
from app.cluster import EventBus, LeaseHeldElsewhere, SessionLease

_SID = "11111111-2222-3333-4444-555555555555"
_OTHER_SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture(autouse=True)
def _isolate_cluster(monkeypatch: pytest.MonkeyPatch):
    """Reset process-wide cluster state so tests can't leak into each other."""
    monkeypatch.delenv("LINCHPIN_INSTANCE_ID", raising=False)
    monkeypatch.setattr(cluster, "_instance_id", None)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(cluster, "_bus", None)
    yield
    cluster._instance_id = None
    cluster._bus = None


# ---------------------------------------------------------------------------
# Instance identity
# ---------------------------------------------------------------------------


class TestInstanceIdentity:
    def test_get_instance_id_is_stable(self):
        first = cluster.get_instance_id()
        assert cluster.get_instance_id() is first

    def test_generated_id_has_api_prefix(self):
        assert cluster.get_instance_id().startswith("api-")

    def test_env_pins_the_identity(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINCHPIN_INSTANCE_ID", "replica-7")
        assert cluster.get_instance_id() == "replica-7"

    def test_set_instance_id_overrides(self):
        cluster.set_instance_id("explicit-id")
        assert cluster.get_instance_id() == "explicit-id"


# ---------------------------------------------------------------------------
# Advisory lock keys and channel names
# ---------------------------------------------------------------------------


class TestAdvisoryKey:
    def test_key_fits_signed_64_bit_range(self):
        key = cluster.advisory_key(_SID)
        assert 0 <= key <= 0x7FFFFFFFFFFFFFFF

    def test_same_session_same_key(self):
        assert cluster.advisory_key(_SID) == cluster.advisory_key(_SID)

    def test_accepts_uuid_object(self):
        assert cluster.advisory_key(uuid.UUID(_SID)) == cluster.advisory_key(_SID)


class TestSessionChannel:
    def test_round_trip(self):
        assert cluster.parse_session_channel(cluster.session_channel(_SID)) == _SID

    def test_non_session_channel_parses_to_none(self):
        assert cluster.parse_session_channel("deployments") is None

    def test_malformed_session_channel_parses_to_none(self):
        assert cluster.parse_session_channel("session_notauuid") is None


# ---------------------------------------------------------------------------
# Lease acquisition (orchestrator wrapper)
# ---------------------------------------------------------------------------


class TestLeaseAcquisition:
    @pytest.mark.asyncio
    async def test_no_pool_degrades_to_unleased(self):
        # db._pool is None (autouse fixture) → get_pool raises RuntimeError
        # inside SessionLease.acquire → the wrapper degrades to None.
        assert await orchestrator._acquire_session_lease(_SID) is None

    @pytest.mark.asyncio
    async def test_held_elsewhere_raises(self):
        with patch.object(orchestrator, "SessionLease") as MockLease:
            mock_lease = MagicMock()
            mock_lease.acquire = AsyncMock(return_value=False)
            MockLease.return_value = mock_lease
            with pytest.raises(LeaseHeldElsewhere):
                await orchestrator._acquire_session_lease(_SID)

    @pytest.mark.asyncio
    async def test_transient_error_degrades_to_unleased(self):
        with patch.object(orchestrator, "SessionLease") as MockLease:
            mock_lease = MagicMock()
            mock_lease.acquire = AsyncMock(side_effect=ValueError("db hiccup"))
            MockLease.return_value = mock_lease
            assert await orchestrator._acquire_session_lease(_SID) is None

    @pytest.mark.asyncio
    async def test_acquired_lease_is_returned(self):
        with patch.object(orchestrator, "SessionLease") as MockLease:
            mock_lease = MagicMock()
            mock_lease.acquire = AsyncMock(return_value=True)
            MockLease.return_value = mock_lease
            lease = await orchestrator._acquire_session_lease(_SID)
            assert lease is mock_lease


# ---------------------------------------------------------------------------
# run_session wrapper wiring
# ---------------------------------------------------------------------------


class TestRunSessionWiring:
    @pytest.mark.asyncio
    async def test_lease_held_elsewhere_never_enters_loop(self):
        with (
            patch.object(
                orchestrator, "_acquire_session_lease",
                AsyncMock(side_effect=LeaseHeldElsewhere("held")),
            ),
            patch.object(orchestrator, "_run_session_loop", new_callable=AsyncMock) as mock_loop,
            patch.object(orchestrator, "_lease_heartbeat", new_callable=AsyncMock),
        ):
            await orchestrator.run_session(_SID, MagicMock())
        mock_loop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_held_lease_runs_loop_with_heartbeat_and_release(self):
        sandbox = MagicMock()
        mock_lease = MagicMock()
        mock_lease.broken = False
        mock_lease.release = AsyncMock()
        with (
            patch.object(orchestrator, "_run_session_loop", new_callable=AsyncMock) as mock_loop,
            patch.object(orchestrator, "_lease_heartbeat", new_callable=AsyncMock) as mock_hb,
        ):
            await orchestrator.run_session(_SID, sandbox, lease=mock_lease)
        mock_loop.assert_awaited_once_with(_SID, sandbox, mock_lease)
        mock_hb.assert_called_once_with(mock_lease, orchestrator.HEARTBEAT_INTERVAL_SEC)
        mock_lease.release.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unleased_run_skips_heartbeat(self):
        sandbox = MagicMock()
        with (
            patch.object(orchestrator, "_acquire_session_lease", AsyncMock(return_value=None)),
            patch.object(orchestrator, "_run_session_loop", new_callable=AsyncMock) as mock_loop,
            patch.object(orchestrator, "_lease_heartbeat", new_callable=AsyncMock) as mock_hb,
        ):
            await orchestrator.run_session(_SID, sandbox)
        mock_loop.assert_awaited_once_with(_SID, sandbox, None)
        mock_hb.assert_not_called()


# ---------------------------------------------------------------------------
# Broken-lease exit inside the main loop
# ---------------------------------------------------------------------------


class TestBrokenLeaseExit:
    @pytest.mark.asyncio
    async def test_broken_lease_exits_before_loading_session(self):
        mock_lease = MagicMock()
        mock_lease.broken = True
        with (
            patch.object(orchestrator, "load_session", new_callable=AsyncMock) as mock_load,
            patch.object(orchestrator, "tracing") as mock_tracing,
        ):
            await orchestrator._run_session_loop(_SID, MagicMock(), mock_lease)
        mock_load.assert_not_awaited()
        mock_tracing.end_session_tracing.assert_called_once_with(_SID)


# ---------------------------------------------------------------------------
# Heartbeat task
# ---------------------------------------------------------------------------


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_returns_on_first_failed_renewal(self):
        mock_lease = MagicMock()
        mock_lease.renew = AsyncMock(return_value=False)
        await asyncio.wait_for(
            orchestrator._lease_heartbeat(mock_lease, 0.001), timeout=1.0
        )
        mock_lease.renew.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_heartbeat_keeps_looping_while_renewals_succeed(self):
        mock_lease = MagicMock()
        mock_lease.renew = AsyncMock(return_value=True)
        task = asyncio.ensure_future(
            orchestrator._lease_heartbeat(mock_lease, 0.001)
        )
        await asyncio.sleep(0.03)
        assert not task.done()
        assert mock_lease.renew.await_count >= 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# EventBus demultiplexing
# ---------------------------------------------------------------------------


class TestEventBusDemultiplex:
    def _bus_with_subs(self):
        bus = EventBus()
        q1 = bus.subscribe(_SID)
        q2 = bus.subscribe(_SID)
        q_other = bus.subscribe(_OTHER_SID)
        return bus, q1, q2, q_other

    def test_payload_routed_to_all_session_subscribers(self):
        bus, q1, q2, q_other = self._bus_with_subs()
        payload = json.dumps({"s": _SID, "k": "user_message"})
        bus._demultiplex(payload)
        assert q1.get_nowait() == payload
        assert q2.get_nowait() == payload
        assert q_other.empty()

    def test_malformed_payload_is_dropped(self):
        bus, q1, q2, q_other = self._bus_with_subs()
        bus._demultiplex("not json at all")
        assert q1.empty() and q2.empty() and q_other.empty()

    def test_non_string_session_id_is_dropped(self):
        bus, q1, q2, q_other = self._bus_with_subs()
        bus._demultiplex(json.dumps({"s": 123, "k": "x"}))
        assert q1.empty() and q2.empty() and q_other.empty()

    def test_unsubscribe_stops_delivery(self):
        bus, q1, _q2, _qo = self._bus_with_subs()
        bus.unsubscribe(_SID, q1)
        payload = json.dumps({"s": _SID, "k": "user_message"})
        bus._demultiplex(payload)
        assert q1.empty()


# ---------------------------------------------------------------------------
# EventBus lifecycle without a pool (degradation)
# ---------------------------------------------------------------------------


class TestBusLifecycle:
    @pytest.mark.asyncio
    async def test_start_event_bus_noop_without_pool(self):
        with patch.object(cluster, "get_pool", side_effect=RuntimeError):
            await cluster.start_event_bus()
        assert cluster._bus is None
        assert not cluster.bus_running()

    @pytest.mark.asyncio
    async def test_stop_event_bus_noop_without_bus(self):
        await cluster.stop_event_bus()  # must not raise
        assert cluster._bus is None

    @pytest.mark.asyncio
    async def test_lease_renew_without_connection_is_false(self):
        lease = SessionLease(_SID)
        assert lease.held is False
        assert await lease.renew() is False

    @pytest.mark.asyncio
    async def test_lease_release_without_connection_is_noop(self):
        lease = SessionLease(_SID)
        await lease.release()  # must not raise


# ---------------------------------------------------------------------------
# app.db.listen — bus mode and fallback mode
# ---------------------------------------------------------------------------


def _make_pool_with_conn():
    """Build a fake asyncpg pool: acquire() awaits to a mock connection and
    also works as an async context manager (db.notify uses ``async with``)."""
    conn = MagicMock()
    conn.add_listener = AsyncMock()
    conn.remove_listener = AsyncMock()
    conn.fetchval = AsyncMock()
    conn.execute = AsyncMock()
    cm = MagicMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire = AsyncMock(return_value=conn)
    pool.release = AsyncMock()
    # For the ``async with pool.acquire()`` form: acquire must be a plain
    # mock returning the context manager (special methods can't be set on
    # instances, so configure them on the default MagicMock protocol).
    pool.acquire_cm = cm
    return pool, conn, cm


class TestDbListen:
    @pytest.mark.asyncio
    async def test_bus_mode_subscribes_and_unsubscribes(self):
        bus = EventBus()
        # Pretend the bus is running without starting a real LISTEN task.
        bus._task = asyncio.ensure_future(asyncio.sleep(3600))
        monkeypatch_bus = bus
        with patch.object(cluster, "_bus", monkeypatch_bus):
            gen = db.listen(cluster.session_channel(_SID))
            task = asyncio.ensure_future(gen.__anext__())
            await asyncio.sleep(0.01)  # let the generator body subscribe
            assert len(bus._subs.get(_SID, ())) == 1

            payload = json.dumps({"s": _SID, "k": "user_message"})
            bus.dispatch(_SID, payload)
            assert await asyncio.wait_for(task, timeout=1.0) == payload

            await gen.aclose()
            await asyncio.sleep(0)
            assert _SID not in bus._subs
        bus._task.cancel()

    @pytest.mark.asyncio
    async def test_fallback_filters_other_sessions(self):
        pool, conn, _cm = _make_pool_with_conn()
        with patch.object(db, "get_pool", return_value=pool):
            gen = db.listen(cluster.session_channel(_SID))
            task = asyncio.ensure_future(gen.__anext__())
            await asyncio.sleep(0.01)

            conn.add_listener.assert_awaited_once()
            target, callback = conn.add_listener.await_args.args
            assert target == cluster.EVENT_CHANNEL

            # A notification for another session must not wake us.
            other_payload = json.dumps({"s": _OTHER_SID, "k": "user_message"})
            callback(None, 0, target, other_payload)
            await asyncio.sleep(0)
            assert not task.done()

            # A malformed payload still wakes the consumer (re-query).
            callback(None, 0, target, "{broken json")
            payload = await asyncio.wait_for(task, timeout=1.0)
            assert payload == "{broken json"

            await gen.aclose()
            conn.remove_listener.assert_awaited_once()
            pool.release.assert_awaited_once_with(conn)


class TestDbNotify:
    @pytest.mark.asyncio
    async def test_session_channel_uses_parameterized_pg_notify_and_local_fanout(self):
        pool, conn, cm = _make_pool_with_conn()
        # db.notify uses ``async with pool.acquire() as conn``.
        pool.acquire = MagicMock(return_value=cm)

        bus = EventBus()
        queue = bus.subscribe(_SID)
        with (
            patch.object(db, "get_pool", return_value=pool),
            patch.object(cluster, "_bus", bus),
        ):
            await db.notify(cluster.session_channel(_SID), "user_message")

        body = json.dumps({"s": _SID, "k": "user_message"})
        # Local fanout arrives immediately, without a Postgres round-trip.
        assert queue.get_nowait() == body
        conn.execute.assert_awaited_once_with(
            "SELECT pg_notify($1, $2)", cluster.EVENT_CHANNEL, body
        )

    @pytest.mark.asyncio
    async def test_non_session_channel_keeps_legacy_notify(self):
        pool, conn, cm = _make_pool_with_conn()
        pool.acquire = MagicMock(return_value=cm)

        with patch.object(db, "get_pool", return_value=pool):
            await db.notify("deployments", "hello")

        sql = conn.execute.await_args.args[0]
        assert sql.startswith("NOTIFY deployments")
        assert "hello" in sql


# ---------------------------------------------------------------------------
# Lease supervisor takeover
# ---------------------------------------------------------------------------


def _stale_row(**overrides):
    row = {
        "id": _SID,
        "status": "running",
        "container_id": "container-1",
        "stop_reason": "end_turn",
        "orchestrator_instance": "api-deadbeef",
    }
    row.update(overrides)
    return row


class TestTakeover:
    async def _run_takeover(self, *, row, tasks=None, mock_lease=None, sandbox=None):
        """Shared driver: patch DB row + lease, return (taken, mocks).

        Only builds default mocks for arguments the caller didn't pass —
        tests pass pre-configured mocks to exercise the error branches.
        """
        if mock_lease is None:
            mock_lease = MagicMock()
            mock_lease.acquire = AsyncMock(return_value=True)
        mock_lease.release = AsyncMock()
        if sandbox is None:
            sandbox = MagicMock()
            sandbox.exec = AsyncMock()  # container alive by default
        tasks = tasks if tasks is not None else {}
        with (
            patch.object(orchestrator, "fetch_all", AsyncMock(return_value=[row])),
            patch.object(orchestrator, "SessionLease", MagicMock(return_value=mock_lease)),
            patch.object(orchestrator, "transition", new_callable=AsyncMock) as mock_transition,
            patch.object(orchestrator, "append_event", new_callable=AsyncMock) as mock_append,
            patch.object(orchestrator, "run_session", new_callable=AsyncMock) as mock_run,
        ):
            taken = await orchestrator.take_over_stale_sessions(
                sandbox, tasks,
                watcher_tasks={}, spawn_watcher=AsyncMock(),
            )
            return taken, tasks, mock_transition, mock_append, mock_run, mock_lease

    @pytest.mark.asyncio
    async def test_alive_container_is_taken_over_with_lease(self):
        taken, tasks, mock_transition, mock_append, mock_run, mock_lease = \
            await self._run_takeover(row=_stale_row())
        assert taken == 1
        assert _SID in tasks
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["lease"] is mock_lease
        mock_transition.assert_awaited_once_with(
            _SID, "idle", stop_reason="end_turn"
        )
        appended = mock_append.await_args_list[0].args
        assert appended[1] == "session.takeover"
        mock_lease.release.assert_not_awaited()
        await asyncio.sleep(0.01)  # let the spawned run_session task finish

    @pytest.mark.asyncio
    async def test_locally_active_session_is_skipped(self):
        active_task = MagicMock()
        active_task.done.return_value = False
        with patch.object(orchestrator, "SessionLease") as MockLease:
            taken, *_ = await self._run_takeover(
                row=_stale_row(), tasks={_SID: active_task}
            )
            MockLease.assert_not_called()
        assert taken == 0

    @pytest.mark.asyncio
    async def test_dead_container_fails_the_session(self):
        sandbox = MagicMock()
        sandbox.exec = AsyncMock(side_effect=RuntimeError("container gone"))
        taken, _, mock_transition, mock_append, _, mock_lease = \
            await self._run_takeover(row=_stale_row(), sandbox=sandbox)
        assert taken == 0
        mock_lease.release.assert_awaited_once()
        mock_transition.assert_awaited_once_with(
            _SID, "failed", stop_reason="container_lost"
        )
        assert mock_append.await_args.args[1] == "session.error"

    @pytest.mark.asyncio
    async def test_missing_container_id_fails_the_session(self):
        taken, _, mock_transition, _, _, mock_lease = \
            await self._run_takeover(row=_stale_row(container_id=None))
        assert taken == 0
        mock_lease.release.assert_awaited_once()
        mock_transition.assert_awaited_once_with(
            _SID, "failed", stop_reason="container_lost"
        )

    @pytest.mark.asyncio
    async def test_lease_lost_elsewhere_is_skipped_without_release(self):
        mock_lease = MagicMock()
        mock_lease.acquire = AsyncMock(return_value=False)
        mock_lease.release = AsyncMock()
        taken, _, mock_transition, _, _, mock_lease = \
            await self._run_takeover(row=_stale_row(), mock_lease=mock_lease)
        assert taken == 0
        mock_lease.release.assert_not_awaited()
        mock_transition.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lease_error_is_skipped(self):
        mock_lease = MagicMock()
        mock_lease.acquire = AsyncMock(side_effect=RuntimeError("no pool"))
        mock_lease.release = AsyncMock()
        taken, _, _, _, _, _ = \
            await self._run_takeover(row=_stale_row(), mock_lease=mock_lease)
        assert taken == 0


class TestSuperviseLoop:
    @pytest.mark.asyncio
    async def test_loop_repeats_until_cancelled(self):
        with patch.object(
            orchestrator, "take_over_stale_sessions", new_callable=AsyncMock
        ) as mock_pass:
            task = asyncio.ensure_future(
                orchestrator.supervise_stale_sessions(
                    MagicMock(), {}, interval_seconds=0.01
                )
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert mock_pass.await_count >= 2


class TestSupervisorEnabled:
    def test_enabled_by_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LINCHPIN_LEASE_SUPERVISOR", raising=False)
        assert orchestrator.supervisor_enabled() is True

    def test_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINCHPIN_LEASE_SUPERVISOR", "false")
        assert orchestrator.supervisor_enabled() is False

    def test_env_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINCHPIN_LEASE_SUPERVISOR", "FALSE")
        assert orchestrator.supervisor_enabled() is False


# ---------------------------------------------------------------------------
# recover_sessions lease-aware scan
# ---------------------------------------------------------------------------


class TestRecoverLeaseAware:
    @pytest.mark.asyncio
    async def test_scan_is_scoped_to_stale_or_orphaned_leases(self):
        with (
            patch.object(orchestrator, "fetch_all", AsyncMock(return_value=[])) as mock_fetch,
            patch.object(orchestrator, "run_session", new_callable=AsyncMock),
        ):
            await orchestrator.recover_sessions(MagicMock(), {})
        sql, args = mock_fetch.await_args.args[0], mock_fetch.await_args.args[1:]
        assert "orchestrator_heartbeat" in sql
        assert args[0] == cluster.get_instance_id()
        assert args[1] == orchestrator.LEASE_TIMEOUT_SEC
