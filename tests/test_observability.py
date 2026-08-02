"""Error tracking (app.observability): no-op without a DSN, real init call with one."""

from __future__ import annotations

import pytest
import sentry_sdk
from app.config import settings
from app.observability import init_error_tracking

# No cleanup fixture needed: sentry_sdk.init() with a fake/unreachable DSN
# (as the tests below use) never blocks or raises — events queue in a
# background thread and fail to send silently — so a real init call leaking
# a globally-installed client between tests in this file is harmless, not a
# source of flakiness elsewhere in the suite.


def test_blank_dsn_never_calls_sentry_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sentry_dsn", "")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))

    init_error_tracking()

    assert calls == []


def test_dsn_configured_calls_sentry_init_with_expected_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sentry_dsn", "https://public@o0.ingest.sentry.io/0")
    monkeypatch.setattr(settings, "sentry_environment", "production")
    monkeypatch.setattr(settings, "sentry_release", "1.2.3")
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", 0.1)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))

    init_error_tracking()

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["dsn"] == "https://public@o0.ingest.sentry.io/0"
    assert kwargs["environment"] == "production"
    assert kwargs["release"] == "1.2.3"
    assert kwargs["traces_sample_rate"] == 0.1
    # Real financial/legal documents flow through this app — never send
    # request bodies/headers to a third-party error tracker.
    assert kwargs["send_default_pii"] is False


def test_blank_release_passes_none_not_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sentry_dsn", "https://public@o0.ingest.sentry.io/0")
    monkeypatch.setattr(settings, "sentry_release", "")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))

    init_error_tracking()

    assert calls[0]["release"] is None


def test_real_init_with_a_dsn_does_not_raise() -> None:
    """Not mocked — genuinely calls sentry_sdk.init with a real-shaped (but
    fake/unreachable) DSN, confirming the SDK itself never blocks/raises on
    a bad or unreachable endpoint (events queue in the background)."""
    sentry_sdk.init(dsn="https://public@o0.ingest.sentry.io/0", traces_sample_rate=0.0)
    assert sentry_sdk.get_client().is_active()
