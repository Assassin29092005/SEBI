"""In-memory sliding-window rate limiter — ASGI middleware.

No external dependency (no Redis, no slowapi) — keeps the zero-infra-for-dev
philosophy.  Per-IP for unauthenticated endpoints (login/register brute-force
protection), per-user for authenticated ones (cost control on LLM-touching
endpoints, general abuse protection on everything else).

Limits are configurable via ``app.config.settings`` and can be disabled
entirely for tests (``RATE_LIMIT_ENABLED=false``).
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth.security import InvalidToken, decode_access_token

# --------------------------------------------------------------------------
# Sliding-window counter
# --------------------------------------------------------------------------

class _SlidingWindow:
    """Thread-safe, per-key sliding-window request counter."""

    def __init__(self) -> None:
        self._lock = Lock()
        # key -> list of timestamps (monotonic)
        self._windows: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str, max_requests: int, window_seconds: float) -> tuple[bool, float]:
        """Check if ``key`` is within its limit.

        Returns ``(allowed, retry_after_seconds)``.  ``retry_after`` is 0.0
        when allowed, otherwise the number of seconds until the oldest
        request in the window expires.
        """
        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            # Prune expired entries. Keys whose window has fully drained are
            # dropped, not left behind as empty lists — otherwise a burst of
            # one-shot keys (a scan hitting login from many IPs) leaves a
            # permanent entry each and the dict only ever grows.
            timestamps = [t for t in self._windows[key] if t > cutoff]
            self._prune(cutoff)

            if len(timestamps) >= max_requests:
                self._windows[key] = timestamps
                retry_after = timestamps[0] - cutoff
                return False, max(retry_after, 0.1)

            timestamps.append(now)
            self._windows[key] = timestamps
            return True, 0.0

    def _prune(self, cutoff: float) -> None:
        """Drop keys with no live requests left. Caller holds the lock.

        ponytail: a full sweep per request is fine at this scale (one
        issuer's team). Switch to a periodic sweep if the key count ever
        gets large enough to show up in latency.
        """
        for stale in [k for k, ts in self._windows.items() if not any(t > cutoff for t in ts)]:
            del self._windows[stale]

    def reset(self) -> None:
        """Clear all windows — test-only."""
        with self._lock:
            self._windows.clear()


_window = _SlidingWindow()


def reset_rate_limiter() -> None:
    """Clear rate-limit state — test cleanup."""
    _window.reset()


# --------------------------------------------------------------------------
# Route → limit classification
# --------------------------------------------------------------------------

# (path_prefix, method_or_None, max_requests_per_minute)
# Most-specific first; first match wins.
_RATE_LIMITS: list[tuple[str, str | None, int]] = [
    # Auth: brute-force protection — keyed by IP, not user
    ("/api/auth/login", "POST", 10),
    ("/api/auth/register", "POST", 10),
    # LLM-touching: cost control — keyed by user
    ("/api/generate", "POST", 5),
    ("/api/validate/examiner/iterative", "POST", 5),
    ("/api/validate/semantic", "GET", 10),
    # Upload: bounded by body-size middleware too, but limit request count
    ("/api/uploads/extract", "POST", 20),
    # Fact writes are legitimately bursty: confirming a wizard-full of answers,
    # or accepting a page of extracted proposals, is two requests per fact in
    # quick succession. Keyed per user and cheap to serve, so the budget is
    # sized for that flow rather than for one-at-a-time human typing — at 60
    # the demo seeder tripped its own API.
    ("/api/facts", None, 240),
    ("/api/proposals/accept", "POST", 240),
    # Regulatory watch: external HTTP call to SEBI
    ("/api/regulatory-watch/check", "POST", 3),
]

_DEFAULT_LIMIT = 60  # requests per minute for all other endpoints
_DEFAULT_BUCKET = "default"
_WINDOW_SECONDS = 60.0

# Paths where the key is the client IP (no auth token available yet)
_IP_KEYED_PATHS = {"/api/auth/login", "/api/auth/register"}


def _classify(path: str, method: str) -> tuple[str, int]:
    """Return ``(bucket, per-minute limit)`` for this path+method.

    The bucket is part of the counter key, so each limit gets its own
    budget. Sharing one counter across limits is a real failure, not a
    theoretical one: an anonymous request falls back to an IP key, so a
    handful of unauthenticated calls under the 60/min default would eat
    into the 10/min login budget on the *same* key and 429 a user before
    they had typed a password.
    """
    for prefix, route_method, limit in _RATE_LIMITS:
        if path.startswith(prefix) and (route_method is None or route_method == method):
            return prefix, limit
    return _DEFAULT_BUCKET, _DEFAULT_LIMIT


def _client_ip(request: Request) -> str:
    # client can be None under some ASGI transports (including the test
    # client), so this is never assumed present.
    return request.client.host if request.client else "unknown"


def _request_key(request: Request, path: str) -> str:
    """Build the rate-limit key: IP for auth endpoints, user id otherwise.

    Keyed on the token's ``sub`` claim rather than the raw token string:
    a user holding two valid tokens (two browsers, or a re-login) is still
    one subject, so the per-user budget can't be doubled by logging in
    again. Verification is a local HMAC check — no DB lookup.
    """
    if path in _IP_KEYED_PATHS:
        return f"ip:{_client_ip(request)}"

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            subject = decode_access_token(auth[len("bearer ") :].strip()).get("sub")
        except InvalidToken:
            subject = None
        if subject:
            return f"user:{subject}"

    # Unauthenticated or bad token — fall back to the caller's IP so an
    # anonymous flood is still bounded.
    return f"ip:{_client_ip(request)}"


# --------------------------------------------------------------------------
# Middleware
# --------------------------------------------------------------------------


async def rate_limit_middleware(request: Request, call_next: Any) -> Any:
    """ASGI middleware: enforce per-key sliding-window rate limits.

    Returns 429 with ``Retry-After`` header when the limit is exceeded.
    Registered before ``audit_log_middleware`` so rate-limited requests
    are still audited (a 429 is itself a security-relevant event).
    """
    path = request.url.path
    method = request.method

    # Health/schema are public, high-frequency, never rate-limited. Neither
    # is anything outside /api: in the container image those paths are the
    # built SPA's own shell and asset files (see the catch-all at the bottom
    # of app.main), and one hard refresh pulling a dozen JS chunks is a
    # normal page load, not abuse.
    if path in {"/api/health", "/api/schema"} or not path.startswith("/api/"):
        return await call_next(request)

    bucket, limit = _classify(path, method)
    key = f"{bucket}|{_request_key(request, path)}"
    allowed, retry_after = _window.is_allowed(key, limit, _WINDOW_SECONDS)

    if not allowed:
        return JSONResponse(
            {"detail": "rate limit exceeded", "retry_after": round(retry_after, 1)},
            status_code=429,
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    return await call_next(request)
