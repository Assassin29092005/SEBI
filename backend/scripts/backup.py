#!/usr/bin/env python3
"""Backup script: pg_dump + encrypted file archive.

Creates a timestamped backup directory containing:
1. A compressed Postgres dump (pg_dump --format=custom) — facts, review
   state, user accounts, and the audit log all live here now.
2. A copy of the encrypted uploads vault (data/uploads/) — original
   uploaded documents, the one durable artefact still on the filesystem.
3. A copy of data/audit/, if present: the pre-Postgres encrypted audit
   log. Nothing writes there any more, but an existing file from before
   the migration is still the only copy of those events.

Retention: keeps the last N backups (default 7), deleting older ones, and
— only after the dump has succeeded — prunes audit events older than
``--audit-retention-days`` from the live database.

Usage:
    python backend/scripts/backup.py                   # uses defaults
    python backend/scripts/backup.py --keep 14         # keep 14 backups
    python backend/scripts/backup.py --output /mnt/bk  # custom output dir

Designed to be cron'd:
    0 2 * * * cd /path/to/SEBI && python backend/scripts/backup.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKUP_DIR = REPO_ROOT / "backups"
DEFAULT_KEEP = 7

# Matches docker-compose.yml defaults — production overrides via env vars
DEFAULT_DB_URL = "postgresql://drhp:drhp_dev_password@localhost:5432/drhp_studio"


def parse_db_url(url: str) -> dict[str, str]:
    """Extract host, port, user, password, dbname from a Postgres URL."""
    # postgresql://user:password@host:port/dbname
    from urllib.parse import urlparse
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "drhp",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/") or "drhp_studio",
    }


def run_backup(
    output_dir: Path = DEFAULT_BACKUP_DIR,
    keep: int = DEFAULT_KEEP,
    db_url: str = DEFAULT_DB_URL,
    audit_retention_days: int = 0,
) -> Path:
    """Run a full backup. Returns the path to the created backup directory."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_dir = output_dir / f"backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    db_params = parse_db_url(db_url)
    pg_env = {**os.environ, "PGPASSWORD": db_params["password"]}
    conn_args = [
        f"--host={db_params['host']}",
        f"--port={db_params['port']}",
        f"--username={db_params['user']}",
        f"--dbname={db_params['dbname']}",
    ]

    # 1. Postgres dump
    dump_path = backup_dir / "database.dump"
    dump_ok = False
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", "--compress=6", *conn_args, f"--file={dump_path}"],
            env=pg_env,
            check=True,
            capture_output=True,
            text=True,
        )
        dump_ok = True
        print(f"  ✓ Database dump: {dump_path} ({dump_path.stat().st_size:,} bytes)")
    except FileNotFoundError:
        print("  ✗ pg_dump not found — skipping database backup", file=sys.stderr)
        print("    Install PostgreSQL client tools to enable database backups.", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"  ✗ pg_dump failed: {e.stderr}", file=sys.stderr)

    # 2. Copy encrypted uploads
    uploads_src = REPO_ROOT / "data" / "uploads"
    if uploads_src.exists():
        uploads_dst = backup_dir / "uploads"
        shutil.copytree(uploads_src, uploads_dst)
        file_count = sum(1 for _ in uploads_dst.rglob("*") if _.is_file())
        print(f"  ✓ Uploads archive: {file_count} encrypted files")
    else:
        print("  · No uploads directory found (skip)")

    # 3. Copy the pre-Postgres encrypted audit log, if one survives
    audit_src = REPO_ROOT / "data" / "audit"
    if audit_src.exists():
        shutil.copytree(audit_src, backup_dir / "audit")
        print("  ✓ Legacy audit-log archive copied")
    else:
        print("  · No legacy audit directory found (skip)")

    # 4. Audit retention — only ever after a successful dump, so the rows
    #    being deleted are already inside the backup taken above.
    if audit_retention_days > 0:
        if dump_ok:
            _prune_audit_events(audit_retention_days, conn_args, pg_env)
        else:
            print(
                "  · Skipping audit prune: no successful dump this run "
                "(would delete rows with no backup)",
                file=sys.stderr,
            )

    # 5. Retention: prune old backups
    all_backups = sorted(
        [d for d in output_dir.iterdir() if d.is_dir() and d.name.startswith("backup_")],
        key=lambda d: d.name,
    )
    while len(all_backups) > keep:
        old = all_backups.pop(0)
        shutil.rmtree(old, ignore_errors=True)
        print(f"  🗑 Pruned old backup: {old.name}")

    print(f"\n✓ Backup complete: {backup_dir}")
    return backup_dir


def _prune_audit_events(days: int, conn_args: list[str], pg_env: dict[str, str]) -> None:
    """Delete audit rows older than ``days``. The one retention policy the
    old flat file never had (see CLAUDE.md's Known Limitations)."""
    statement = f"DELETE FROM audit_events WHERE at < now() - interval '{int(days)} days'"
    try:
        result = subprocess.run(
            ["psql", *conn_args, "--quiet", "--no-align", "--tuples-only", "-c", statement],
            env=pg_env,
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"  ✓ Audit retention: {result.stdout.strip() or 'DELETE 0'} (older than {days}d)")
    except FileNotFoundError:
        print("  ✗ psql not found — skipping audit retention prune", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"  ✗ audit prune failed: {e.stderr}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="DRHP Studio backup")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_BACKUP_DIR, help="Backup output directory"
    )
    parser.add_argument(
        "--keep", type=int, default=DEFAULT_KEEP, help="Number of backups to retain"
    )
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="Postgres connection URL")
    parser.add_argument(
        "--audit-retention-days",
        type=int,
        default=int(os.environ.get("AUDIT_RETENTION_DAYS", "0")),
        help="Delete audit_events older than this after a successful dump (0 = keep forever)",
    )
    args = parser.parse_args()

    print(f"DRHP Studio Backup — {datetime.now(UTC).isoformat()}")
    print(f"  Output: {args.output}")
    print(f"  Retention: keep last {args.keep}")
    print()

    run_backup(
        output_dir=args.output,
        keep=args.keep,
        db_url=args.db_url,
        audit_retention_days=args.audit_retention_days,
    )


if __name__ == "__main__":
    main()
