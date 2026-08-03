"""Standalone regulatory-staleness check — no running backend/DB required.

Loads the checklist schema's pinned ``amended_through`` date and compares it
against SEBI's real, live ICDR-tagged postings (see ``app.regulatory_watch``).
Meant to run on a schedule independent of any deployed instance (see
``.github/workflows/regulatory-staleness.yml``) — a demo checks the pinned
date once at build time; this is the "continuously, in production" half of
that same claim, since there is no always-on server here to poll instead.

Exit codes:
  0 — clean (checked successfully, nothing newer than the pin) or the live
      check itself couldn't run (network hiccup, site changed shape) — a
      soft signal, not something that should page anyone or fail a build
      on its own.
  1 — checked successfully AND found something newer than the pin. This is
      the actionable case: go look at what's listed and decide whether the
      schema needs a human-reviewed update.

Never auto-updates the schema — see CLAUDE.md: every schema change is
human-reviewed, this only routes "go check this" to a human.

Usage: python backend/scripts/check_regulatory_staleness.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.regulatory_watch import check_for_staleness  # noqa: E402
from app.schema.loader import load_checklist  # noqa: E402


async def _run() -> int:
    checklist = load_checklist()
    pinned = date.fromisoformat(checklist.header.amended_through)
    print(f"Schema pinned as amended through: {pinned.isoformat()}")
    print("Checking SEBI's public ICDR-tagged postings for anything newer...")

    result = await check_for_staleness(pinned)

    if not result.checked_successfully:
        print(
            "\nCheck did not complete (network issue or SEBI's site layout may "
            "have changed) — treating this as inconclusive, not a failure.",
        )
        return 0

    if not result.newer_updates:
        print(f"\nClean: nothing newer than {pinned.isoformat()} found ({result.source}).")
        return 0

    print(
        f"\n{len(result.newer_updates)} ICDR-tagged SEBI publication(s) newer than "
        f"{pinned.isoformat()} ({result.source}):\n",
    )
    for update in result.newer_updates:
        print(f"  {update.published.isoformat()}  {update.title}")
        print(f"      {update.url}")
    print(
        "\nThis does not mean the schema is out of date — most of these will be "
        "consultation papers, informal guidance, or enforcement notices, not "
        "notified amendments. It means a human should go look. See CLAUDE.md's "
        "Known Limitations for what this watcher can and can't tell you.",
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
