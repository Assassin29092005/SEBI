"""In-memory sliding-window rate limiter — abuse protection for a single-
process deployment.

Two tiers, wired in as ASGI middleware in ``app.main`` (``rate_limit_middleware``):

- ``auth``: ``/api/auth/login`` and ``/api/auth/register`` — a strict limit
  keyed by client IP (there is no bearer token yet at this point), sized to
  blunt credential stuffing and registration spam without blocking a real
  user who mistypes a password a few times.
- ``default``: every other endpoint (except ``/api/health``) — a looser
  limit keyed by the authenticated user (from the bearer token, decoded
  in-process, no DB lookup) when one is present, falling back to client IP
  for anonymous requests. Per-user keying means one abusive account can't
  hide behind a shared office/NAT IP, and one abusive IP doesn't collaterally
  throttle other legitimate users behind the same IP.

Sliding-window LOG (not fixed-window counters): each key's bucket is the
list of recent hit timestamps, trimmed to the window on every check. This
avoids the classic fixed-window bug where a client can burst up to 2x the
limit by timing requests across a window boundary.

Known limitation, documented rather than hidden: this is in-process memory,
not a shared store (Redis or similar). It is correct for exactly the
single-process deployment this app currently targets (see
``Settings.rate_limit_*`` in ``app.config``); horizontally scaling this API
behind a load balancer would need a real distributed limiter, since each
process would otherwise enforce the limit independently and the *effective*
limit would multiply by the process count.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    """At most ``limit`` calls per ``window_seconds`` per key.

    ``now`` is injectable so tests can drive the clock deterministically
    instead of racing real wall-clock time.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._now = now
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str) -> tuple[bool, float]:
        """Returns ``(allowed, retry_after_seconds)``.

        Records this call as a hit only when it's allowed — a rejected call
        must not itself count against the window, or a client parked exactly
        at the limit could never recover once the window slides.
        """
        now = self._now()
        cutoff = now - self._window
        timestamps = [t for t in self._hits.get(key, ()) if t > cutoff]

        if len(timestamps) >= self._limit:
            # Bucket is full: keep the trimmed list (bounds memory even for a
            # key that never succeeds again) and report when the oldest hit
            # in the window ages out.
            self._hits[key] = timestamps
            return False, max(timestamps[0] + self._window - now, 0.0)

        timestamps.append(now)
        self._hits[key] = timestamps
        return True, 0.0

    def reset(self) -> None:
        """Discard all recorded hits.

        Test-only — mirrors ``app.audit.reset_audit_log()``: without this,
        the module-level limiter instances in ``app.main`` would carry state
        across every test in a pytest run (all sharing one process, and in
        practice one client identity), and a handful of tests would start
        seeing spurious 429s once the count of test-issued requests crossed
        the configured limit.
        """
        self._hits.clear()
