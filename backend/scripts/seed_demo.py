"""Seed the running app's fact store from the demo company fixtures.

Pushes every fact in ``data/demo_company/wizard_answers.json`` through the
real API (``POST /api/facts`` then ``POST /api/facts/{id}/confirm``) so the
confirmation step is exercised the same way the wizard exercises it — this
is a shortcut past manual data entry, not a way around confirmation.

Every endpoint now requires a bearer token (see app.auth): this script
registers (or logs into, on a repeat run) a demo promoter account first and
attaches the token to every request that follows.

Facts, review state, and the demo promoter account are durable in Postgres
(see app.db) — re-running this script against the same database logs into
the existing demo account rather than erroring, and adds to whatever facts
are already there. For a clean slate, reset the database (e.g. drop and
re-``alembic upgrade head``) before re-seeding.

Usage (from the repo root, with the backend running on 127.0.0.1:8000):

    python backend/scripts/seed_demo.py
    python backend/scripts/seed_demo.py --with-uploads   # also runs the
                                                           # extraction demo,
                                                           # including the
                                                           # planted issue-size
                                                           # contradiction
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = REPO_ROOT / "data" / "demo_company"

DEMO_PROMOTER_EMAIL = os.environ.get("DEMO_PROMOTER_EMAIL", "promoter@sunriseagrotech.example")
DEMO_PROMOTER_NAME = "Sunrise Agrotech Promoter"

# The default is committed, so it is public — fine for a laptop, not for a
# deployment anyone can reach. Seeding a public instance with it leaves an
# account whose password is readable in this file, and a passer-by could sign
# in and edit the demo data mid-presentation. Override for anything reachable:
#   DEMO_PROMOTER_PASSWORD='...' python backend/scripts/seed_demo.py --base-url https://...
DEMO_PROMOTER_PASSWORD = os.environ.get("DEMO_PROMOTER_PASSWORD", "SunriseDemo!2026")


class _RetryOn429(httpx.BaseTransport):
    """Wait out the API's own rate limiter instead of falling over on it.

    Seeding pushes two requests per fact (add, then confirm) as fast as the
    network allows — comfortably past the per-minute budget in
    ``app.rate_limit``. The right response for a legitimate bulk client is to
    honour the ``Retry-After`` the server just asked for, not to relax the
    limit for everyone else. Installed once on the client, so every call site
    below gets it without knowing about it.
    """

    def __init__(self, inner: httpx.BaseTransport, max_retries: int = 6) -> None:
        self._inner = inner
        self._max_retries = max_retries

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            response = self._inner.handle_request(request)
            if response.status_code != 429 or attempt == self._max_retries:
                return response
            response.read()
            response.close()
            # +0.5s so we land after the window rolls, not exactly on it.
            wait = float(response.headers.get("retry-after", "1")) + 0.5
            print(f"  · rate limited, waiting {wait:.0f}s", flush=True)
            time.sleep(wait)
        return response


def _authenticate(client: httpx.Client) -> str:
    """Register the demo promoter (idempotent) and return a bearer token.

    First run on a fresh backend: registers. Any later run (backend
    restarted, script re-invoked): the account already exists (register
    answers 409), so this falls back to logging in.
    """
    register = client.post(
        "/api/auth/register",
        json={
            "email": DEMO_PROMOTER_EMAIL,
            "name": DEMO_PROMOTER_NAME,
            "password": DEMO_PROMOTER_PASSWORD,
            "role": "promoter",
        },
    )
    if register.status_code == 200:
        return register.json()["access_token"]
    login = client.post(
        "/api/auth/login",
        json={"email": DEMO_PROMOTER_EMAIL, "password": DEMO_PROMOTER_PASSWORD},
    )
    login.raise_for_status()
    return login.json()["access_token"]


def _seed_wizard_answers(client: httpx.Client) -> int:
    answers: dict[str, object] = json.loads(
        (DEMO_DIR / "wizard_answers.json").read_text(encoding="utf-8")
    )
    count = 0
    for key, value in answers.items():
        fact = client.post(
            "/api/facts",
            json={
                "key": key,
                "value": value,
                "provenance": {"kind": "wizard", "detail": f"seed:wizard_answers.json:{key}"},
                "confidence": 1.0,
                "supplied_by": "promoter",
            },
        ).raise_for_status().json()
        client.post(f"/api/facts/{fact['fact_id']}/confirm").raise_for_status()
        count += 1
    return count


def _seed_uploads(client: httpx.Client) -> int:
    """Run extraction + accept + confirm over every demo upload.

    Deliberately includes ``bank_sanction_letter.txt``, whose stale
    ``issue_size_paise`` figure conflicts with the wizard answer — this is
    the planted contradiction the validation suite is meant to catch live
    (see data/demo_company/README.md).
    """
    count = 0
    for path in sorted((DEMO_DIR / "uploads").glob("*.txt")):
        with path.open("rb") as fh:
            proposals = client.post(
                "/api/uploads/extract",
                files={"file": (path.name, fh, "text/plain")},
            ).raise_for_status().json()
        for proposal in proposals:
            fact = client.post("/api/proposals/accept", json=proposal).raise_for_status().json()
            client.post(f"/api/facts/{fact['fact_id']}/confirm").raise_for_status()
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--with-uploads",
        action="store_true",
        help="also extract + confirm the demo uploads (includes the planted contradiction)",
    )
    args = parser.parse_args()

    with httpx.Client(
        base_url=args.base_url,
        timeout=30.0,
        transport=_RetryOn429(httpx.HTTPTransport()),
    ) as client:
        try:
            client.get("/api/health").raise_for_status()
        except httpx.HTTPError as exc:
            print(f"Backend not reachable at {args.base_url}: {exc}", file=sys.stderr)
            print("Start it first: uvicorn app.main:app --reload", file=sys.stderr)
            raise SystemExit(1) from exc

        token = _authenticate(client)
        client.headers["Authorization"] = f"Bearer {token}"
        print(f"Authenticated as {DEMO_PROMOTER_EMAIL} (promoter).")

        wizard_count = _seed_wizard_answers(client)
        print(f"Seeded and confirmed {wizard_count} wizard facts.")

        if args.with_uploads:
            upload_count = _seed_uploads(client)
            print(f"Seeded and confirmed {upload_count} facts from uploads.")
            print(
                "Note: issue_size_paise now has two confirmed versions "
                "(wizard vs. bank_sanction_letter.txt) — this is the planted "
                "contradiction. Run POST /api/generate then GET "
                "/api/validate/contradictions to see it caught."
            )

        print("Done. Now call POST /api/generate, then explore /api/gaps, "
              "/api/validate/*, /api/coverage, and /api/coverage/benchmark.")


if __name__ == "__main__":
    main()
