"""Session persistence: snapshot/restore of the mutable demo state.

Design note (demo-grade, per CLAUDE.md "demo-ready over production-ready"):
this is a single snapshot on disk, not a database. One file holds the fact
store contents (issuer financials, PAN/CIN, litigation records — the facts
this whole app exists to collect), the banker review state, and the last
generated sections, so a backend restart mid-demo does not lose the session.
The snapshot is encrypted at rest (see ``app.crypto``) — this is real
issuer data now, not disposable demo state. Writes are atomic — serialise to
a ``.tmp`` sibling, then ``os.replace`` over the real file — so a crash
mid-write can never leave a torn snapshot; readers see either the previous
complete snapshot or the new one. A corrupt/unreadable/undecryptable
snapshot is logged and ignored (the app boots empty rather than crashing).
Production would still want a real database (Postgres + migrations,
per-tenant isolation) over a single flat file — documented here, not built.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.crypto import DecryptionError, decrypt_bytes, encrypt_bytes
from app.facts import Fact, FactStore
from app.generate.sections import GeneratedSection
from app.review.workflow import ReviewState

logger = logging.getLogger("drhp.persistence")

# ``.enc`` (not ``.json``): the file is ciphertext, not readable JSON — the
# extension should not lie about that.
SNAPSHOT_FILENAME = "session.enc"


class SessionSnapshot(BaseModel):
    """Everything mutable in :class:`app.main.AppState` that is worth reviving.

    The litigation connector is deliberately absent — it is a stateless mock
    recreated by ``create_state()``. The checklist never appears here either:
    it is versioned schema, loaded from YAML at import time.
    """

    facts: list[Fact]
    review_state: ReviewState
    generated_sections: list[GeneratedSection]
    saved_at: str  # ISO 8601 UTC timestamp of the save


def _snapshot_path(directory: Path | None) -> Path:
    return (directory if directory is not None else settings.session_dir) / SNAPSHOT_FILENAME


def save_snapshot(
    facts: list[Fact],
    review_state: ReviewState,
    generated_sections: list[GeneratedSection],
    directory: Path | None = None,
) -> Path:
    """Atomically write the session snapshot; returns the snapshot path.

    Write-to-temp-then-``os.replace`` guarantees the snapshot file is only
    ever observed in a complete state, even if the process dies mid-write.
    """
    snapshot = SessionSnapshot(
        facts=facts,
        review_state=review_state,
        generated_sections=generated_sections,
        saved_at=datetime.now(UTC).isoformat(),
    )
    target = _snapshot_path(directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    plaintext = snapshot.model_dump_json(indent=2).encode("utf-8")
    tmp.write_bytes(encrypt_bytes(plaintext))
    os.replace(tmp, target)
    return target


def load_snapshot(directory: Path | None = None) -> SessionSnapshot | None:
    """Load the snapshot if present, decryptable, and parseable; otherwise ``None``.

    Never raises: a missing file means a fresh session, and a corrupt,
    unreadable, or undecryptable (wrong/missing ``ENCRYPTION_KEY``) file is
    logged as a warning and treated the same way — a bad snapshot must not be
    able to crash the app at boot.
    """
    target = _snapshot_path(directory)
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:  # unreadable (permissions, transient IO) — boot empty
        logger.warning("Session snapshot at %s unreadable, starting fresh: %s", target, exc)
        return None
    try:
        plaintext = decrypt_bytes(raw)
    except DecryptionError as exc:
        logger.warning(
            "Session snapshot at %s could not be decrypted, starting fresh: %s", target, exc
        )
        return None
    try:
        return SessionSnapshot.model_validate_json(plaintext)
    except ValidationError as exc:
        logger.warning("Session snapshot at %s is corrupt, starting fresh: %s", target, exc)
        return None


def clear_snapshot(directory: Path | None = None) -> None:
    """Delete the snapshot if present. Safe to call when none exists."""
    _snapshot_path(directory).unlink(missing_ok=True)


def restore_fact_store(facts: list[Fact]) -> FactStore:
    """Rebuild a :class:`FactStore` from snapshotted facts.

    ``FactStore.add`` stores the given ``Fact`` verbatim keyed by its own
    ``fact_id``, so identifiers, confirmation flags, and provenance (including
    ``supersedes`` chains) survive exactly — ``confirmed_by_key`` on the
    restored store behaves identically to the original.
    """
    store = FactStore()
    for fact in facts:
        store.add(fact)
    return store
