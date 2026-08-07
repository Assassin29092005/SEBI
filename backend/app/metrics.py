"""Simple in-memory request metrics — no Prometheus dependency.

Provides per-endpoint counters (total requests, errors, latency buckets)
and a ``GET /api/metrics`` endpoint (banker-only) that returns the counters
as JSON.  Hooks into the existing audit middleware flow via
``record_request``.

Intentionally lightweight. Prometheus + Grafana is what this would be
swapped for once there is somewhere to ship metrics to; in-process counters
are what make the app observable without adding infrastructure to run
alongside it. The trade-off is real and bounded: state is per-process, so it
resets on restart and does not aggregate across instances.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from pydantic import BaseModel

# Per-endpoint latency samples kept for the percentile. Counts and totals
# below are exact and unbounded; only the latency *sample* is windowed, so
# the collector's memory is O(endpoints), not O(requests served). Without
# this the process leaks one tuple per request for as long as it runs.
_LATENCY_WINDOW = 512


class EndpointMetrics(BaseModel):
    path: str
    total_requests: int = 0
    error_count: int = 0  # status >= 400
    avg_latency_ms: float = 0.0
    #: 95th percentile over the last ``_LATENCY_WINDOW`` requests to this
    #: endpoint, not over all time.
    p95_latency_ms: float = 0.0
    status_codes: dict[int, int] = {}


class AppMetrics(BaseModel):
    uptime_seconds: float
    total_requests: int
    total_errors: int
    requests_per_minute: float
    endpoints: list[EndpointMetrics]


class _MetricsCollector:
    """Thread-safe in-memory metrics collector."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._start_time = time.monotonic()
        self._total_requests = 0
        self._total_errors = 0
        self._counts: dict[str, int] = defaultdict(int)
        self._errors: dict[str, int] = defaultdict(int)
        self._latency_sum: dict[str, float] = defaultdict(float)
        self._status_codes: dict[str, dict[int, int]] = defaultdict(dict)
        self._latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=_LATENCY_WINDOW)
        )

    def record(self, route: str, status_code: int, latency_ms: float) -> None:
        """Record a completed request.

        ``route`` must be a bounded label (``"GET /api/facts/{id}"``), not a
        raw path with ids substituted in — see the call site in
        ``app.main.audit_log_middleware``.
        """
        with self._lock:
            self._total_requests += 1
            if status_code >= 400:
                self._total_errors += 1
                self._errors[route] += 1
            self._counts[route] += 1
            self._latency_sum[route] += latency_ms
            self._latencies[route].append(latency_ms)
            codes = self._status_codes[route]
            codes[status_code] = codes.get(status_code, 0) + 1

    def snapshot(self) -> AppMetrics:
        """Return a point-in-time snapshot of all metrics."""
        with self._lock:
            uptime = time.monotonic() - self._start_time
            rpm = (self._total_requests / uptime * 60) if uptime > 0 else 0.0

            endpoints: list[EndpointMetrics] = []
            for route, total in sorted(self._counts.items()):
                latencies = sorted(self._latencies[route])
                if latencies:
                    p95_idx = min(int(len(latencies) * 0.95), len(latencies) - 1)
                    p95_lat = latencies[p95_idx]
                else:
                    p95_lat = 0.0

                endpoints.append(EndpointMetrics(
                    path=route,
                    total_requests=total,
                    error_count=self._errors.get(route, 0),
                    avg_latency_ms=round(self._latency_sum[route] / total, 2) if total else 0.0,
                    p95_latency_ms=round(p95_lat, 2),
                    status_codes=dict(self._status_codes[route]),
                ))

            return AppMetrics(
                uptime_seconds=round(uptime, 1),
                total_requests=self._total_requests,
                total_errors=self._total_errors,
                requests_per_minute=round(rpm, 2),
                endpoints=endpoints,
            )

    def reset(self) -> None:
        """Clear all metrics — test-only."""
        with self._lock:
            self._start_time = time.monotonic()
            self._total_requests = 0
            self._total_errors = 0
            self._counts.clear()
            self._errors.clear()
            self._latency_sum.clear()
            self._status_codes.clear()
            self._latencies.clear()


_collector = _MetricsCollector()


def get_metrics_collector() -> _MetricsCollector:
    return _collector


def reset_metrics() -> None:
    _collector.reset()
