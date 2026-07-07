"""Seed the running app's fact store from the demo company fixtures.

Pushes every fact in ``data/demo_company/wizard_answers.json`` through the
real API (``POST /api/facts`` then ``POST /api/facts/{id}/confirm``) so the
confirmation step is exercised the same way the wizard exercises it — this
is a shortcut past manual data entry, not a way around confirmation.

State is in-memory on the server (see app.main.AppState); restart the
backend for a clean slate before re-seeding.

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
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = REPO_ROOT / "data" / "demo_company"


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

    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        try:
            client.get("/api/health").raise_for_status()
        except httpx.HTTPError as exc:
            print(f"Backend not reachable at {args.base_url}: {exc}", file=sys.stderr)
            print("Start it first: uvicorn app.main:app --reload", file=sys.stderr)
            raise SystemExit(1) from exc

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
