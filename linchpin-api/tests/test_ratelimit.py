"""Atlas M5 (FR-11.2) — in-process sliding-window rate limiter.

Pure unit tests: the limiter's clock is faked so window math and
``Retry-After`` values are exact.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import ratelimit
from app.ratelimit import check_rate_limit, reset_rate_limits

KEY = "11111111-1111-1111-1111-111111111111"


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _clean_state():
    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(monotonic=fake))
    return fake


# --- when limiting does not apply ---------------------------------------------


def test_no_limit_is_unlimited():
    assert check_rate_limit(KEY, None) is None


def test_bootstrap_key_is_never_limited():
    assert check_rate_limit(None, 1) is None


# --- window behavior -----------------------------------------------------------


def test_requests_under_the_limit_pass(clock):
    for _ in range(3):
        assert check_rate_limit(KEY, 3) is None


def test_limit_exceeded_returns_retry_after(clock):
    for _ in range(3):
        check_rate_limit(KEY, 3)
    retry = check_rate_limit(KEY, 3)
    assert retry == int(ratelimit.WINDOW_SECONDS) + 1  # oldest is "now"


def test_retry_after_counts_down_to_oldest_request(clock):
    check_rate_limit(KEY, 1)
    clock.now += 25
    assert check_rate_limit(KEY, 1) == 36  # int(60 - 25) + 1


def test_retry_after_is_never_below_one_second(clock):
    check_rate_limit(KEY, 1)
    clock.now += ratelimit.WINDOW_SECONDS - 0.2  # 0.2s from expiry
    assert check_rate_limit(KEY, 1) == 1


def test_window_slides_open_again(clock):
    check_rate_limit(KEY, 1)
    clock.now += ratelimit.WINDOW_SECONDS + 1
    assert check_rate_limit(KEY, 1) is None


def test_rejected_requests_consume_no_slot(clock):
    check_rate_limit(KEY, 2)
    check_rate_limit(KEY, 2)
    for _ in range(10):
        assert check_rate_limit(KEY, 2) is not None
    # The bucket still holds only the two accepted timestamps.
    assert len(ratelimit._buckets[KEY]) == 2


# --- memory bounds --------------------------------------------------------------


def test_lru_evicts_oldest_key(monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_TRACKED_KEYS", 2)
    check_rate_limit("a", 5)
    check_rate_limit("b", 5)
    check_rate_limit("c", 5)  # "a" is the least-recently-seen key
    assert "a" not in ratelimit._buckets
    assert set(ratelimit._buckets) == {"b", "c"}


def test_touch_refreshes_lru_order(monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_TRACKED_KEYS", 2)
    check_rate_limit("a", 5)
    check_rate_limit("b", 5)
    check_rate_limit("a", 5)  # refresh "a" — "b" becomes LRU
    check_rate_limit("c", 5)  # "b" evicted instead
    assert "a" in ratelimit._buckets
    assert "b" not in ratelimit._buckets


def test_reset_rate_limits_clears_state(clock):
    check_rate_limit(KEY, 5)
    reset_rate_limits()
    assert ratelimit._buckets == {}
    assert not ratelimit._lru
    # Fresh window: the same key is admissible again even at limit 1.
    assert check_rate_limit(KEY, 1) is None
