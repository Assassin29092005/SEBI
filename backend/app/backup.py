"""Backup and disaster recovery: periodic full backups of everything durable
this app owns — Postgres (facts, review state, user accounts) plus the two
encrypted-at-rest directories that live outside the database (archived
original uploads, the audit log).

Scope, stated plainly: this is PERIODIC FULL-DUMP backup (``pg_dump`` +
a directory copy into one timestamped archive), triggered by whoever/whatever
runs :mod:`backend.scripts.backup_data` (cron, Windows Task Scheduler, a CI
job — this app has no scheduler of its own; see CLAUDE.md's Known
Limitations). It is **not** continuous point-in-time recovery — that needs
WAL archiving, a genuinely different and heavier mechanism. An outage loses
everything since the last successful backup run, exactly as long as the
configured cadence. That is an honest, real disaster-recovery posture
appropriate for this project's scale (one issuer's drafting-cycle data), not
the strongest one possible — the previous state ("a Postgres restart is
durable, there is no backup at all") was the actual gap this closes.

Requires the Postgres client tools (``pg_dump`` / ``psql``) as system
binaries — same optional-real-capability pattern as Tesseract for OCR
(:mod:`app.intake.ocr`): pip installs the async driver (``asyncpg``), not the
CLI tools, so they don't ship with this repo. ``pg_dump_cmd``/``psql_cmd``
only need setting when the binary isn't on ``PATH`` (the common case on
Windows).

Uses plain-SQL dumps (``pg_dump`` with no ``--format`` flag), not the
custom/directory formats — restorable with plain ``psql`` alone (one fewer
required binary than the ``pg_restore``-based alternative) and the dump
itself is inspectable text, consistent with this project's preference for
transparency over opacity.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.config import settings

_BACKUP_PREFIX = "drhp_backup_"
_BACKUP_SUFFIX = ".tar.gz"
# Sortable lexicographically == chronologically, and filesystem-safe (no
# colons). Microsecond resolution (%f), not just seconds: two backups
# triggered within the same second — a re-run script, or two quick clicks on
# the banker dashboard's "back up now" button — would otherwise collide on
# the same filename and silently overwrite each other.
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


class BackupError(Exception):
    """``pg_dump`` failed, or a source directory couldn't be read."""


class RestoreError(Exception):
    """``psql`` restore failed, or the archive is missing/malformed/incomplete."""


class BackupInfo(BaseModel):
    filename: str
    size_bytes: int
    created_at: datetime


@functools.lru_cache(maxsize=1)
def is_pg_dump_available() -> bool:
    return shutil.which(settings.pg_dump_cmd) is not None


@functools.lru_cache(maxsize=1)
def is_psql_available() -> bool:
    return shutil.which(settings.psql_cmd) is not None


def _cli_connection_string(database_url: str) -> str:
    """SQLAlchemy async URL -> plain libpq connection string ``pg_dump``/``psql`` understand.

    ``postgresql+asyncpg://...`` -> ``postgresql://...`` — the CLI tools don't
    know what an ``asyncpg`` driver suffix is.
    """
    scheme, sep, rest = database_url.partition("://")
    if not sep:
        return database_url
    return f"{scheme.split('+', 1)[0]}://{rest}"


def _run_pg_dump(database_url: str, out_path: Path) -> None:
    if not is_pg_dump_available():
        raise BackupError(
            f"{settings.pg_dump_cmd!r} not found on PATH — install the Postgres "
            "client tools and/or set PG_DUMP_CMD in .env (see .env.example)."
        )
    result = subprocess.run(
        [
            settings.pg_dump_cmd,
            "--no-owner",
            "--file",
            str(out_path),
            _cli_connection_string(database_url),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BackupError(f"pg_dump failed (exit {result.returncode}): {result.stderr.strip()}")


def _run_psql_restore(database_url: str, dump_path: Path) -> None:
    if not is_psql_available():
        raise RestoreError(
            f"{settings.psql_cmd!r} not found on PATH — install the Postgres "
            "client tools and/or set PSQL_CMD in .env (see .env.example)."
        )
    result = subprocess.run(
        [
            settings.psql_cmd,
            "--set=ON_ERROR_STOP=1",
            "--file",
            str(dump_path),
            _cli_connection_string(database_url),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RestoreError(
            f"psql restore failed (exit {result.returncode}): {result.stderr.strip()}"
        )


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------


def _existing_backups(out_dir: Path) -> list[Path]:
    if not out_dir.exists():
        return []
    return sorted(out_dir.glob(f"{_BACKUP_PREFIX}*{_BACKUP_SUFFIX}"))


def _apply_retention(out_dir: Path, retention: int) -> list[Path]:
    """Delete the oldest backups beyond *retention* count. Returns the deleted paths.

    ``retention <= 0`` disables pruning entirely (an explicit choice, not the
    default — the default in ``Settings.backup_retention_count`` is 7).
    """
    if retention <= 0:
        return []
    backups = _existing_backups(out_dir)
    if len(backups) <= retention:
        return []
    to_delete = backups[: len(backups) - retention]
    for path in to_delete:
        path.unlink()
    return to_delete


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------


def create_backup(
    *,
    out_dir: Path | None = None,
    database_url: str | None = None,
    uploads_dir: Path | None = None,
    audit_dir: Path | None = None,
    retention: int | None = None,
) -> Path:
    """Create one timestamped, self-contained backup archive and return its path.

    Contents: ``db.sql`` (plain-SQL ``pg_dump``), ``uploads/`` (verbatim copy
    of the archived-upload vault — already encrypted, see :mod:`app.crypto`),
    ``audit/`` (verbatim copy of the audit log — likewise encrypted),
    ``manifest.json`` (creation time and what was included). Uploads/audit
    are included only if their directories exist, so a fresh install with no
    uploads yet still backs up cleanly.

    Written to ``<out_dir>/<archive>.tar.gz.tmp`` first and atomically
    renamed into place, so a failed or interrupted backup run never leaves a
    half-written archive at the final name (same pattern as
    :func:`app.assemble.bundle.build_bundle`). Retention is applied last,
    after the new backup lands, so a failed backup never reduces the count of
    good backups on disk.
    """
    out_dir = out_dir if out_dir is not None else settings.backup_dir
    database_url = database_url if database_url is not None else settings.database_url
    uploads_dir = uploads_dir if uploads_dir is not None else settings.uploads_dir
    audit_dir = audit_dir if audit_dir is not None else settings.audit_dir
    retention = retention if retention is not None else settings.backup_retention_count

    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime(_TIMESTAMP_FORMAT)
    archive_path = out_dir / f"{_BACKUP_PREFIX}{timestamp}{_BACKUP_SUFFIX}"
    tmp_archive = archive_path.with_suffix(archive_path.suffix + ".tmp")

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        dump_path = tmp / "db.sql"
        _run_pg_dump(database_url, dump_path)

        uploads_included = uploads_dir.exists()
        audit_included = audit_dir.exists()
        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "uploads_included": uploads_included,
            "audit_included": audit_included,
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        with tarfile.open(tmp_archive, "w:gz") as tar:
            tar.add(dump_path, arcname="db.sql")
            tar.add(manifest_path, arcname="manifest.json")
            if uploads_included:
                tar.add(uploads_dir, arcname="uploads")
            if audit_included:
                tar.add(audit_dir, arcname="audit")

    tmp_archive.replace(archive_path)
    _apply_retention(out_dir, retention)
    return archive_path


def list_backups(out_dir: Path | None = None) -> list[BackupInfo]:
    """Existing backups in *out_dir*, most recent first."""
    out_dir = out_dir if out_dir is not None else settings.backup_dir
    infos = [
        BackupInfo(
            filename=path.name,
            size_bytes=path.stat().st_size,
            created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        )
        for path in _existing_backups(out_dir)
    ]
    return sorted(infos, key=lambda i: i.created_at, reverse=True)


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


def restore_backup(
    archive_path: Path,
    *,
    confirm: bool,
    database_url: str | None = None,
    uploads_dir: Path | None = None,
    audit_dir: Path | None = None,
) -> None:
    """Restore Postgres + uploads + audit log from *archive_path*.

    **Destructive**: overwrites the target database (via ``psql``, running
    every statement in the dump against it) and replaces the uploads/audit
    directories outright. ``confirm`` has no default — every caller must
    decide explicitly; a stale default here is exactly the kind of bug that
    destroys a live deployment's data by accident. There is no HTTP endpoint
    for this: restore is deliberately CLI-only (see
    ``backend/scripts/restore_data.py``, which additionally requires ``--yes``
    on the command line before calling this at all) — too consequential to
    ever be one authenticated request away from triggering.
    """
    if not confirm:
        raise RestoreError("restore_backup() requires confirm=True — this overwrites live data.")

    database_url = database_url if database_url is not None else settings.database_url
    uploads_dir = uploads_dir if uploads_dir is not None else settings.uploads_dir
    audit_dir = audit_dir if audit_dir is not None else settings.audit_dir

    if not archive_path.exists():
        raise RestoreError(f"backup archive not found: {archive_path}")

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                # filter="data": strips path-traversal/absolute-path/device-file
                # entries before anything touches disk — this archive should
                # only ever be one this app produced itself, but a restore is
                # exactly the kind of operation where "should" isn't good
                # enough if someone hands you an archive from an unknown source.
                tar.extractall(tmp, filter="data")
        except tarfile.TarError as exc:
            raise RestoreError(f"could not read {archive_path} as a backup archive: {exc}") from exc

        manifest_path = tmp / "manifest.json"
        dump_path = tmp / "db.sql"
        if not manifest_path.exists() or not dump_path.exists():
            raise RestoreError(
                f"{archive_path} is missing manifest.json/db.sql — not a valid backup"
            )

        _run_psql_restore(database_url, dump_path)

        extracted_uploads = tmp / "uploads"
        if extracted_uploads.exists():
            if uploads_dir.exists():
                shutil.rmtree(uploads_dir)
            shutil.copytree(extracted_uploads, uploads_dir)

        extracted_audit = tmp / "audit"
        if extracted_audit.exists():
            if audit_dir.exists():
                shutil.rmtree(audit_dir)
            shutil.copytree(extracted_audit, audit_dir)
