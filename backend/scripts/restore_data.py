"""Restore Postgres + archived uploads + audit log from a backup archive.

**DESTRUCTIVE.** This overwrites the current database (every statement in
the backup's SQL dump is run against it) and replaces the uploads/audit
directories outright. There is no undo short of restoring an earlier backup.
Deliberately CLI-only — no HTTP endpoint calls into this, ever (see
``app.backup.restore_backup``'s docstring for why).

Usage (from the repo root):

    python backend/scripts/restore_data.py data/backups/drhp_backup_20260731T120000Z.tar.gz --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backup import RestoreError, restore_backup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="path to a drhp_backup_*.tar.gz archive")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="required — confirms you intend to overwrite the current database and files",
    )
    args = parser.parse_args()

    if not args.yes:
        print(
            "Refusing to restore without --yes. This OVERWRITES the current "
            "database and the uploads/audit directories — there is no undo.",
            file=sys.stderr,
        )
        return 1

    try:
        restore_backup(args.archive, confirm=True)
    except RestoreError as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1

    print(f"Restored from {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
