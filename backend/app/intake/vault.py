"""Encrypted-at-rest archive of original uploaded documents.

Extraction (``app.intake.uploads``) only ever needed the file bytes in
memory for the duration of one request — the original document itself was
never kept anywhere, so a banker's due-diligence review had nothing to check
a fact's snippet against except the snippet text itself. This module fixes
that: every upload is archived, encrypted (see :mod:`app.crypto`), and
retrievable by any authenticated user via ``GET /api/uploads`` /
``GET /api/uploads/{document_id}`` (see ``app.main``) — the same read access
as the fact store, since a banker/auditor reviewing a promoter's facts needs
the source document, not just the extracted claim.

One file per document (``<uuid>.enc``), each holding an encrypted JSON
envelope of ``{metadata, content_base64}`` — metadata lives inside the
envelope too (not a plaintext sidecar) because a filename can itself be
sensitive (e.g. a counterparty's name in a litigation-notice filename).
"""

from __future__ import annotations

import logging
import os
from base64 import b64decode, b64encode
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from app.config import settings
from app.crypto import DecryptionError, decrypt_bytes, encrypt_bytes
from app.schema.models import Role

logger = logging.getLogger("drhp.intake.vault")


class ArchivedDocumentMeta(BaseModel):
    document_id: str
    filename: str
    content_type: str
    uploaded_by: Role
    uploaded_at: datetime
    size_bytes: int


class _Envelope(BaseModel):
    meta: ArchivedDocumentMeta
    content_b64: str


def _path(document_id: str) -> Path:
    return settings.uploads_dir / f"{document_id}.enc"


def archive_upload(
    content: bytes, filename: str, content_type: str, uploaded_by: Role
) -> ArchivedDocumentMeta:
    """Encrypt and persist the original upload; returns its metadata (never the bytes)."""
    meta = ArchivedDocumentMeta(
        document_id=str(uuid4()),
        filename=filename,
        content_type=content_type or "application/octet-stream",
        uploaded_by=uploaded_by,
        uploaded_at=datetime.now(UTC),
        size_bytes=len(content),
    )
    envelope = _Envelope(meta=meta, content_b64=b64encode(content).decode("ascii"))
    path = _path(meta.document_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(encrypt_bytes(envelope.model_dump_json().encode("utf-8")))
    os.replace(tmp, path)
    return meta


def retrieve_upload(document_id: str) -> tuple[ArchivedDocumentMeta, bytes] | None:
    """Decrypt and return ``(metadata, original_bytes)``, or ``None`` if missing/corrupt.

    A path-traversal-shaped ``document_id`` (e.g. ``"../../etc/passwd"``)
    cannot escape ``uploads_dir``: only exact ``<uuid>.enc`` filenames written
    by :func:`archive_upload` ever exist there, and any other id simply
    resolves to a nonexistent file — but the id is still resolved through
    ``Path.name`` first so a malformed id can never even be interpreted as a
    relative path component.
    """
    safe_id = Path(document_id).name
    path = _path(safe_id)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("archived document %s unreadable: %s", safe_id, exc)
        return None
    try:
        plaintext = decrypt_bytes(raw)
    except DecryptionError as exc:
        logger.warning("archived document %s could not be decrypted: %s", safe_id, exc)
        return None
    envelope = _Envelope.model_validate_json(plaintext)
    return envelope.meta, b64decode(envelope.content_b64)


def list_archived_uploads() -> list[ArchivedDocumentMeta]:
    """Metadata for every archived document, oldest first. Skips any that fail to decrypt."""
    if not settings.uploads_dir.exists():
        return []
    metas: list[ArchivedDocumentMeta] = []
    for path in sorted(settings.uploads_dir.glob("*.enc")):
        try:
            plaintext = decrypt_bytes(path.read_bytes())
            metas.append(_Envelope.model_validate_json(plaintext).meta)
        except (DecryptionError, OSError, ValueError):
            continue
    return sorted(metas, key=lambda m: m.uploaded_at)
