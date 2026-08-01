"""Create a full backup archive: Postgres + archived uploads + audit log.

Talks to Postgres directly via ``pg_dump`` (no running API server needed —
useful precisely when the API server is the thing that's down) and reads
``Settings`` the same way the app does, so it honours whatever ``.env`` the
deployment actually uses.

This script does not schedule itself. Run it from cron / Windows Task
Scheduler for a real backup cadence — see CLAUDE.md's Known Limitations for
why the app has no scheduler of its own to do this automatically.

Usage (from the repo root):

    python backend/scripts/backup_data.py
    python backend/scripts/backup_data.py --out-dir /mnt/backups --retention 14
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backup import BackupError, create_backup  # noqa: E402
from app.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path, default=None, help=f"default: {settings.backup_dir}"
    )
    parser.add_argument(
        "--retention",
        type=int,
        default=None,
        help=(
            "how many recent backups to keep, 0 disables pruning "
            f"(default: {settings.backup_retention_count})"
        ),
    )
    args = parser.parse_args()

    try:
        path = create_backup(out_dir=args.out_dir, retention=args.retention)
    except BackupError as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1

    print(f"Backup written to {path} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
