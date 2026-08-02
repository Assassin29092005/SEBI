"""Error tracking — optional, real, no-op when unconfigured.

Same optional-real-integration pattern as every other external capability in
this codebase (``app.llm.client``, ``app.intake.litigation``,
``app.intake.ocr``, ``app.backup``): a blank ``SENTRY_DSN`` means
:func:`init_error_tracking` simply returns without calling ``sentry_sdk
.init`` — zero import-time behavior change, zero outbound network calls, so
local dev and the test suite are unaffected. Set ``SENTRY_DSN`` (a free
sentry.io project, or a self-hosted instance) to start capturing unhandled
exceptions and explicit ``logger.exception()``/``logger.error()`` calls.

Must be called BEFORE the FastAPI app is constructed (see ``app.main``) —
sentry-sdk auto-instruments FastAPI/Starlette by detecting them installed at
init time; no manual middleware wiring needed here.

``send_default_pii`` is hardcoded ``False``: this app handles real
financial/legal documents, and request bodies/headers must never leave the
process even to an error tracker.
"""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger("drhp.observability")


def init_error_tracking() -> None:
    if not settings.sentry_dsn:
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        release=settings.sentry_release or None,
        # Performance tracing costs quota on a free plan and this app has no
        # SLO to justify it yet — capture errors, not traces, by default.
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )
    logger.info("Error tracking initialised (environment=%s)", settings.sentry_environment)
