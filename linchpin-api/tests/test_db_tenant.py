"""Atlas M6 (C1 phase-2) — RLS tenant-context wiring in app.db.

Validates: the query helpers mirror the request-scoped tenant context
(set by the auth dependency) onto pooled connections as the
``app.current_project`` GUC and RESET it before the connection returns
to the pool. With no context set (background jobs, pre-auth lookups)
the helpers issue zero extra round-trips — fail-open is "no statements",
not "a statement that allows everything". The fail-open row-visibility
semantics themselves live in migration 0030's policies and are verified
against a real database in the ATLAS plan's smoke checks.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.db import _guc_state, execute, fetch_all, fetch_one, set_tenant_context

SET_GUC = "SELECT set_config('app.current_project', $1, false)"
RESET_GUC = "RESET app.current_project"


class FakeConn:
    """asyncpg.Connection stand-in.

    Records GUC bookkeeping statements issued via execute() separately
    from main-query reads, so tests can assert on *extra* round-trips.
    Plain class (no __slots__) → weakref-able as a WeakKeyDictionary key.
    """

    def __init__(self, *, row=None, rows=None, fail_reset=False):
        self.executed: list[tuple[str, tuple]] = []
        self.reads: list[tuple[str, tuple]] = []
        self.row = row
        self.rows = rows if rows is not None else []
        self.fail_reset = fail_reset

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if self.fail_reset and "RESET" in sql:
            raise RuntimeError("boom: reset failed")
        return "OK"

    async def fetchrow(self, sql, *args):
        self.reads.append((sql, args))
        return self.row

    async def fetch(self, sql, *args):
        self.reads.append((sql, args))
        return self.rows


class FakePool:
    def __init__(self, conn: FakeConn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pytest.fixture(autouse=True)
def _clean_tenant_context():
    """Keep the request-scoped contextvar and connection-state map from
    leaking between tests."""
    set_tenant_context(None)
    _guc_state.clear()
    yield
    set_tenant_context(None)
    _guc_state.clear()


@pytest.mark.asyncio
async def test_no_tenant_context_means_zero_extra_round_trips():
    """Background / pre-auth path (context never set): helpers issue no
    GUC statements at all — fail-open costs nothing."""
    sentinel = {"id": 1}
    conn = FakeConn(row=sentinel)
    with patch("app.db.get_pool", return_value=FakePool(conn)):
        row = await fetch_one("SELECT * FROM sessions WHERE id = $1", "s1")

    assert row is sentinel
    assert conn.executed == []  # no set_config, no RESET
    assert len(conn.reads) == 1  # only the query itself


@pytest.mark.asyncio
async def test_fetch_one_sets_and_resets_tenant_guc():
    """A request-scoped query pins the GUC on acquire and RESETs it
    before the connection goes back to the pool."""
    pid = str(uuid.uuid4())
    set_tenant_context(pid)
    conn = FakeConn(row={"id": 2})
    with patch("app.db.get_pool", return_value=FakePool(conn)):
        row = await fetch_one("SELECT * FROM sessions WHERE id = $1", "s1")

    assert row == {"id": 2}
    assert conn.executed == [
        (SET_GUC, (pid,)),
        (RESET_GUC, ()),
    ]


@pytest.mark.asyncio
async def test_execute_and_fetch_all_wrap_tenant_guc():
    """execute() and fetch_all() apply the same GUC pair; the main query
    runs between set and RESET."""
    pid = str(uuid.uuid4())
    set_tenant_context(pid)

    conn = FakeConn()
    with patch("app.db.get_pool", return_value=FakePool(conn)):
        status = await execute("DELETE FROM sessions WHERE id = $1", "s1")

    assert status == "OK"
    assert [sql for sql, _ in conn.executed] == [
        SET_GUC,
        "DELETE FROM sessions WHERE id = $1",
        RESET_GUC,
    ]

    conn2 = FakeConn(rows=[{"id": 1}])
    with patch("app.db.get_pool", return_value=FakePool(conn2)):
        rows = await fetch_all("SELECT * FROM sessions")

    assert rows == [{"id": 1}]
    assert conn2.executed == [(SET_GUC, (pid,)), (RESET_GUC, ())]


@pytest.mark.asyncio
async def test_reset_failure_is_suppressed():
    """A failing RESET must not fail the caller's query — a broken
    connection is discarded by the pool anyway."""
    pid = str(uuid.uuid4())
    set_tenant_context(pid)
    conn = FakeConn(row={"id": 3}, fail_reset=True)
    with patch("app.db.get_pool", return_value=FakePool(conn)):
        row = await fetch_one("SELECT 1")

    assert row == {"id": 3}  # result still returned


@pytest.mark.asyncio
async def test_guc_state_cleared_after_helpers():
    """_guc_state tracks the RESET so the next acquire of the same
    connection re-applies (or skips) the GUC correctly."""
    pid = str(uuid.uuid4())
    set_tenant_context(pid)
    conn = FakeConn(row={"id": 4})
    with patch("app.db.get_pool", return_value=FakePool(conn)):
        await fetch_one("SELECT 1")

    assert _guc_state[conn] is None
