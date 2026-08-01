"""Backup & disaster recovery (app.backup): archive assembly, retention,
restore extraction, and (where the real binary is present) actual pg_dump
subprocess behavior.

Same pattern as test_ocr.py for the system-binary boundary: real-pg_dump-
execution tests are marked ``@pytest.mark.skipif(not is_pg_dump_available(), ...)``,
everything else (archive assembly, retention, restore-side extraction) is
exercised deterministically by monkeypatching the two subprocess-calling
functions (``_run_pg_dump``/``_run_psql_restore``) so it needs no live
Postgres at all.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from app.backup import (
    BackupError,
    RestoreError,
    _apply_retention,
    _cli_connection_string,
    _existing_backups,
    create_backup,
    is_pg_dump_available,
    is_psql_available,
    list_backups,
    restore_backup,
)
from app.config import settings


@pytest.fixture(autouse=True)
def _clear_availability_cache() -> None:
    """``is_pg_dump_available``/``is_psql_available`` are process-cached."""
    is_pg_dump_available.cache_clear()
    is_psql_available.cache_clear()
    yield
    is_pg_dump_available.cache_clear()
    is_psql_available.cache_clear()


# --------------------------------------------------------------------------
# Availability detection (real — exercises whatever this machine's true
# PATH state is)
# --------------------------------------------------------------------------


def test_is_pg_dump_available_matches_real_environment_state() -> None:
    assert isinstance(is_pg_dump_available(), bool)


def test_is_psql_available_matches_real_environment_state() -> None:
    assert isinstance(is_psql_available(), bool)


def test_pg_dump_unavailable_raises_backup_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "pg_dump_cmd", "/definitely/not/a/real/pg_dump/binary")
    is_pg_dump_available.cache_clear()
    with pytest.raises(BackupError, match="not found on PATH"):
        create_backup(out_dir=tmp_path, database_url="postgresql://x/y")


def test_psql_unavailable_raises_restore_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archive = _fake_archive(tmp_path)
    monkeypatch.setattr(settings, "psql_cmd", "/definitely/not/a/real/psql/binary")
    is_psql_available.cache_clear()
    with pytest.raises(RestoreError, match="not found on PATH"):
        restore_backup(archive, confirm=True, database_url="postgresql://x/y")


# --------------------------------------------------------------------------
# Connection-string translation
# --------------------------------------------------------------------------


def test_cli_connection_string_strips_async_driver_suffix() -> None:
    assert (
        _cli_connection_string("postgresql+asyncpg://drhp:pw@localhost:5432/drhp_studio")
        == "postgresql://drhp:pw@localhost:5432/drhp_studio"
    )


def test_cli_connection_string_leaves_plain_url_unchanged() -> None:
    assert (
        _cli_connection_string("postgresql://drhp:pw@localhost:5432/drhp_studio")
        == "postgresql://drhp:pw@localhost:5432/drhp_studio"
    )


# --------------------------------------------------------------------------
# create_backup — deterministic, _run_pg_dump mocked (no live Postgres)
# --------------------------------------------------------------------------


def _stub_pg_dump(monkeypatch: pytest.MonkeyPatch, contents: bytes = b"-- fake dump\n") -> None:
    def _fake(database_url: str, out_path: Path) -> None:
        out_path.write_bytes(contents)

    monkeypatch.setattr("app.backup._run_pg_dump", _fake)


def test_create_backup_produces_a_well_formed_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_pg_dump(monkeypatch, b"-- dump contents\n")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    (uploads_dir / "abc123.enc").write_bytes(b"ciphertext")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "audit.enc").write_bytes(b"ciphertext2")
    out_dir = tmp_path / "backups"

    archive = create_backup(
        out_dir=out_dir,
        database_url="postgresql://x/y",
        uploads_dir=uploads_dir,
        audit_dir=audit_dir,
        retention=7,
    )

    assert archive.parent == out_dir
    assert archive.name.startswith("drhp_backup_") and archive.name.endswith(".tar.gz")
    assert not archive.with_suffix(archive.suffix + ".tmp").exists()  # no leftover tmp file

    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
        # tarfile.add() on a directory adds an entry for the directory itself
        # too, not just the files inside it.
        assert names == {
            "db.sql",
            "manifest.json",
            "uploads",
            "uploads/abc123.enc",
            "audit",
            "audit/audit.enc",
        }
        assert tar.extractfile("db.sql").read() == b"-- dump contents\n"
        manifest = json.loads(tar.extractfile("manifest.json").read())
        assert manifest["uploads_included"] is True
        assert manifest["audit_included"] is True
        assert "created_at" in manifest


def test_create_backup_without_uploads_or_audit_dirs_still_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fresh install, nothing archived/audited yet — must not crash."""
    _stub_pg_dump(monkeypatch)
    archive = create_backup(
        out_dir=tmp_path / "backups",
        database_url="postgresql://x/y",
        uploads_dir=tmp_path / "no-such-uploads",
        audit_dir=tmp_path / "no-such-audit",
        retention=7,
    )
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert names == {"db.sql", "manifest.json"}


def test_create_backup_pg_dump_failure_propagates_as_backup_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _fail(database_url: str, out_path: Path) -> None:
        raise BackupError("pg_dump failed (exit 1): connection refused")

    monkeypatch.setattr("app.backup._run_pg_dump", _fail)
    with pytest.raises(BackupError, match="connection refused"):
        create_backup(out_dir=tmp_path / "backups", database_url="postgresql://x/y")
    # A failed backup must not leave a partial archive at the final name.
    assert list((tmp_path / "backups").glob("*.tar.gz")) == []


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------


def _touch_backup(out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_bytes(b"x")
    return path


def test_retention_keeps_only_the_most_recent_n(tmp_path: Path) -> None:
    names = [f"drhp_backup_2026073{i}T000000Z.tar.gz" for i in range(1, 6)]  # 5 backups
    for name in names:
        _touch_backup(tmp_path, name)

    deleted = _apply_retention(tmp_path, retention=2)

    remaining = {p.name for p in _existing_backups(tmp_path)}
    assert remaining == set(names[-2:])  # the two lexicographically-latest
    assert {p.name for p in deleted} == set(names[:-2])


def test_retention_zero_disables_pruning(tmp_path: Path) -> None:
    for i in range(5):
        _touch_backup(tmp_path, f"drhp_backup_2026073{i}T000000Z.tar.gz")
    deleted = _apply_retention(tmp_path, retention=0)
    assert deleted == []
    assert len(_existing_backups(tmp_path)) == 5


def test_retention_noop_when_under_the_limit(tmp_path: Path) -> None:
    _touch_backup(tmp_path, "drhp_backup_20260731T000000Z.tar.gz")
    deleted = _apply_retention(tmp_path, retention=7)
    assert deleted == []


def test_create_backup_applies_retention_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_pg_dump(monkeypatch)
    out_dir = tmp_path / "backups"
    for _ in range(3):
        create_backup(out_dir=out_dir, database_url="postgresql://x/y", retention=2)
    assert len(_existing_backups(out_dir)) == 2


# --------------------------------------------------------------------------
# list_backups
# --------------------------------------------------------------------------


def test_list_backups_empty_dir_returns_empty_list(tmp_path: Path) -> None:
    assert list_backups(tmp_path / "does-not-exist") == []


def test_list_backups_reports_size_and_is_most_recent_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_pg_dump(monkeypatch, b"first")
    out_dir = tmp_path / "backups"
    first = create_backup(out_dir=out_dir, database_url="postgresql://x/y", retention=0)
    _stub_pg_dump(monkeypatch, b"second, a bit longer")
    second = create_backup(out_dir=out_dir, database_url="postgresql://x/y", retention=0)

    infos = list_backups(out_dir)
    assert [i.filename for i in infos] == [second.name, first.name]
    assert infos[0].size_bytes > 0


# --------------------------------------------------------------------------
# restore_backup
# --------------------------------------------------------------------------


def _fake_archive(tmp_path: Path, *, with_uploads: bool = True, with_audit: bool = True) -> Path:
    """Build a real, well-formed backup archive without touching Postgres."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "db.sql").write_text("-- fake dump\n", encoding="utf-8")
    (src / "manifest.json").write_text(
        json.dumps({"created_at": "2026-07-31T00:00:00Z", "uploads_included": with_uploads,
                    "audit_included": with_audit}),
        encoding="utf-8",
    )
    if with_uploads:
        (src / "uploads").mkdir()
        (src / "uploads" / "a.enc").write_bytes(b"upload-ciphertext")
    if with_audit:
        (src / "audit").mkdir()
        (src / "audit" / "audit.enc").write_bytes(b"audit-ciphertext")

    archive = tmp_path / "drhp_backup_20260731T000000Z.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for member in src.iterdir():
            tar.add(member, arcname=member.name)
    return archive


def test_restore_requires_explicit_confirm(tmp_path: Path) -> None:
    archive = _fake_archive(tmp_path)
    with pytest.raises(RestoreError, match="confirm=True"):
        restore_backup(archive, confirm=False)


def test_restore_missing_archive_raises(tmp_path: Path) -> None:
    with pytest.raises(RestoreError, match="not found"):
        restore_backup(tmp_path / "no-such-file.tar.gz", confirm=True, database_url="postgresql://x/y")


def test_restore_rejects_archive_missing_manifest_or_dump(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.tar.gz"
    with tarfile.open(bogus, "w:gz") as tar:
        info = tarfile.TarInfo("not_a_backup.txt")
        data = b"hello"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(RestoreError, match="not a valid backup"):
        restore_backup(bogus, confirm=True, database_url="postgresql://x/y")


def test_restore_extracts_uploads_and_audit_and_calls_psql(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = _fake_archive(tmp_path)
    calls: list[tuple[str, Path]] = []

    def _fake_restore(database_url: str, dump_path: Path) -> None:
        calls.append((database_url, dump_path))
        assert dump_path.read_text(encoding="utf-8") == "-- fake dump\n"

    monkeypatch.setattr("app.backup._run_psql_restore", _fake_restore)

    uploads_dir = tmp_path / "restored_uploads"
    audit_dir = tmp_path / "restored_audit"
    restore_backup(
        archive,
        confirm=True,
        database_url="postgresql://x/y",
        uploads_dir=uploads_dir,
        audit_dir=audit_dir,
    )

    assert len(calls) == 1
    assert calls[0][0] == "postgresql://x/y"
    assert (uploads_dir / "a.enc").read_bytes() == b"upload-ciphertext"
    assert (audit_dir / "audit.enc").read_bytes() == b"audit-ciphertext"


def test_restore_overwrites_existing_uploads_and_audit_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = _fake_archive(tmp_path)
    monkeypatch.setattr("app.backup._run_psql_restore", lambda database_url, dump_path: None)

    uploads_dir = tmp_path / "restored_uploads"
    uploads_dir.mkdir()
    (uploads_dir / "stale.enc").write_bytes(b"old data that must not survive")

    restore_backup(archive, confirm=True, database_url="postgresql://x/y", uploads_dir=uploads_dir)

    assert not (uploads_dir / "stale.enc").exists()
    assert (uploads_dir / "a.enc").read_bytes() == b"upload-ciphertext"


def test_restore_without_uploads_or_audit_in_archive_leaves_target_dirs_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = _fake_archive(tmp_path, with_uploads=False, with_audit=False)
    monkeypatch.setattr("app.backup._run_psql_restore", lambda database_url, dump_path: None)

    uploads_dir = tmp_path / "restored_uploads"
    uploads_dir.mkdir()
    (uploads_dir / "kept.enc").write_bytes(b"still here")

    restore_backup(archive, confirm=True, database_url="postgresql://x/y", uploads_dir=uploads_dir)

    assert (uploads_dir / "kept.enc").read_bytes() == b"still here"


# --------------------------------------------------------------------------
# Real pg_dump execution — skipped on machines without a Postgres client install
# --------------------------------------------------------------------------


@pytest.mark.skipif(not is_pg_dump_available(), reason="pg_dump is not installed")
def test_real_pg_dump_against_unreachable_host_raises_backup_error(tmp_path: Path) -> None:
    """No live Postgres in CI/this sandbox — pg_dump itself must fail cleanly,
    not crash the caller with a raw subprocess traceback."""
    with pytest.raises(BackupError):
        create_backup(
            out_dir=tmp_path,
            database_url="postgresql://nobody:nothing@127.0.0.1:1/does_not_exist",
            retention=0,
        )


@pytest.mark.skipif(not is_psql_available(), reason="psql is not installed")
def test_real_psql_against_unreachable_host_raises_restore_error(tmp_path: Path) -> None:
    archive = _fake_archive(tmp_path)
    with pytest.raises(RestoreError):
        restore_backup(
            archive,
            confirm=True,
            database_url="postgresql://nobody:nothing@127.0.0.1:1/does_not_exist",
        )
