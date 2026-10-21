"""Atlas M5 (FR-11.2) — in-process sliding-window rate limiting per API key.

Each DB-issued key carries an optional ``rate_limit_rpm`` (requests per
minute). The limiter keeps a sliding window of the last 60 seconds of
accepted request timestamps per key id; a rejected request does not
consume a slot.

Scope and semantics:

- **Bootstrap keys are never limited** (``api_key_id is None``) — that is
  the operator/ops channel and limiting it could lock out administration.
- Keys with ``rate_limit_rpm IS NULL`` are unlimited (default after
  migration 0029, keeps existing keys unaffected).
- The buckets live in process memory: with N API replicas the effective
  ceiling is ``N x limit`` (per-replica enforcement). Precise global
  limiting belongs at the gateway — docs/limits.md carries the APISIX
  recipe. The window also doubles as an upper bound on the in-process
  effect of a limit change (same semantics as the 60s auth cache).
- Buckets are LRU-capped at ``_MAX_TRACKED_KEYS`` to bound memory from
  one-off keys.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque

WINDOW_SECONDS = 60.0
_MAX_TRACKED_KEYS = 10_000

# api_key_id -> timestamps (monotonic) of accepted requests in-window.
_buckets: dict[str, deque[float]] = {}
_lru: OrderedDict[str, None] = OrderedDict()


def check_rate_limit(api_key_id: str | None, limit_rpm: int | None) -> int | None:
    """Check a request against the sliding window.

    Returns the ``Retry-After`` value in seconds when the limit is
    exceeded, or ``None`` when the request is accepted (or limiting does
    not apply).
    """
    if limit_rpm is None or api_key_id is None:
        return None

    now = time.monotonic()

    bucket = _buckets.get(api_key_id)
    if bucket is None:
        bucket = deque()
        _buckets[api_key_id] = bucket
        _lru[api_key_id] = None
        # Evict the least-recently-seen key when over the memory cap.
        while len(_lru) > _MAX_TRACKED_KEYS:
            evicted, _ = _lru.popitem(last=False)
            _buckets.pop(evicted, None)
    else:
        _lru.move_to_end(api_key_id)

    cutoff = now - WINDOW_SECONDS
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()

    if len(bucket) >= limit_rpm:
        # Seconds until the oldest in-window request slides out.
        retry_after = int(WINDOW_SECONDS - (now - bucket[0])) + 1
        return max(retry_after, 1)

    bucket.append(now)
    return None


def reset_rate_limits() -> None:
    """Clear all buckets (test hook)."""
    _buckets.clear()
    _lru.clear()
