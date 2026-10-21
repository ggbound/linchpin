"""Atlas B2 — unit tests for the in-process per-session interrupt registry."""

from __future__ import annotations

import asyncio

import pytest

from app import interrupts


@pytest.fixture(autouse=True)
def _clean_flags():
    """Isolate the process-wide flag registry per test."""
    interrupts._flags.clear()
    yield
    interrupts._flags.clear()


class TestRequestInterrupt:
    def test_is_interrupted_initially_false(self):
        assert interrupts.is_interrupted("s1") is False

    def test_request_sets_flag(self):
        interrupts.request_interrupt("s1")
        assert interrupts.is_interrupted("s1") is True

    def test_request_is_idempotent(self):
        interrupts.request_interrupt("s1")
        interrupts.request_interrupt("s1")
        assert interrupts.is_interrupted("s1") is True

    def test_sessions_are_isolated(self):
        interrupts.request_interrupt("s1")
        assert interrupts.is_interrupted("s2") is False
        assert interrupts.is_interrupted("s1") is True


class TestClearInterrupt:
    def test_clear_resets_flag(self):
        interrupts.request_interrupt("s1")
        interrupts.clear_interrupt("s1")
        assert interrupts.is_interrupted("s1") is False

    def test_clear_unknown_session_is_noop(self):
        interrupts.clear_interrupt("never-requested")

    def test_clear_only_targets_one_session(self):
        interrupts.request_interrupt("s1")
        interrupts.request_interrupt("s2")
        interrupts.clear_interrupt("s1")
        assert interrupts.is_interrupted("s1") is False
        assert interrupts.is_interrupted("s2") is True


class TestEvent:
    def test_event_is_singleton_per_session(self):
        assert interrupts.event("s1") is interrupts.event("s1")

    def test_event_is_distinct_across_sessions(self):
        assert interrupts.event("s1") is not interrupts.event("s2")

    @pytest.mark.asyncio
    async def test_wait_returns_immediately_when_already_set(self):
        interrupts.request_interrupt("s1")
        await asyncio.wait_for(interrupts.event("s1").wait(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_wait_unblocks_pending_waiter(self):
        """The tool-dispatch race parks a waiter before the interrupt
        arrives; request_interrupt must wake it."""
        waiter = asyncio.ensure_future(interrupts.event("s1").wait())
        await asyncio.sleep(0)
        assert not waiter.done()

        interrupts.request_interrupt("s1")
        await asyncio.wait_for(waiter, timeout=0.1)

    def test_clear_then_request_creates_fresh_event(self):
        old = interrupts.event("s1")
        interrupts.request_interrupt("s1")
        interrupts.clear_interrupt("s1")
        # The popped event object is still set, but a fresh request gets a
        # new unset event — consumers re-fetch via event()/is_interrupted().
        fresh = interrupts.event("s1")
        assert fresh is not old
        assert not fresh.is_set()
        interrupts.request_interrupt("s1")
        assert interrupts.is_interrupted("s1") is True
