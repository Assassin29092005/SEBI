"""Exchange-ready ZIP bundle: one download containing the full audit trail.

Pure assembly of payloads the pipeline already produced — no LLM, no fact
access beyond the store's public API, no regeneration. The caller assembles
the two .docx files first (via :func:`app.assemble.docx_builder.assemble`)
and hands their paths in; :func:`build_bundle` never assembles documents.

Audit-trail completeness choice
-------------------------------
``facts_with_provenance.json`` deliberately contains **every** fact in the
store — confirmed AND unconfirmed, superseded versions included. The bundle
is the exchange/banker-facing audit trail: a reviewer must be able to see
what was proposed but never confirmed, and what a correction replaced, not
just the facts that fed generation. (Generation itself still consumes only
confirmed, non-superseded facts — that rule lives in the fact store and the
generator, not here.)

``generated_sections.json`` is likewise included so every citation span in
the assembled documents can be traced back to its fact id without opening
the .docx files.

The archive is written to ``<out_path>.tmp`` and atomically renamed into
place, so a failed build never leaves a half-written bundle at ``out_path``.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from app.coverage import CoverageReport
from app.facts import FactStore
from app.generate.sections import GeneratedSection
from app.review.workflow import ReviewState
from app.schema.models import Checklist
from app.validate.contradictions import Contradiction
from app.validate.examiner import Objection
from app.validate.gaps import GapReport

#: Manifest disclaimer — the bundle is a draft package, never a filing.
BUNDLE_NOTE = (
    "Draft package — requires merchant banker certification before any regulatory submission."
)

#: Every bundle member, in write order. ``manifest.json`` is last so it can
#: list the complete member set (itself included). The set is deterministic:
#: the same members appear in every bundle, whatever the pipeline state.
BUNDLE_MEMBERS: tuple[str, ...] = (
    "drhp.docx",
    "abridged.docx",
    "gap_report.json",
    "contradictions.json",
    "coverage.json",
    "examiner_objections.json",
    "arithmetic_findings.json",
    "generated_sections.json",
    "facts_with_provenance.json",
    "review_state.json",
    "manifest.json",
)


def _items_json(items: Sequence[object]) -> str:
    """Wrap a list payload as ``{"items": [...]}`` (Pydantic-aware, indent=2)."""
    dumped = [
        item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in items
    ]
    return json.dumps({"items": dumped}, indent=2, ensure_ascii=False, default=str)


def build_bundle(
    *,
    checklist: Checklist,
    sections: list[GeneratedSection],
    store: FactStore,
    review_state: ReviewState,
    gaps: GapReport,
    contradictions: list[Contradiction],
    coverage: CoverageReport,
    objections: list[Objection],
    arithmetic: list,  # arithmetic-check findings; item type owned by its checker module
    drhp_path: Path,
    abridged_path: Path,
    out_path: Path,
) -> Path:
    """Build the exchange-ready ZIP at ``out_path`` and return that path.

    Contents (stdlib :mod:`zipfile`, ``ZIP_DEFLATED``):

    - ``drhp.docx`` / ``abridged.docx`` — copied byte-for-byte from
      ``drhp_path`` / ``abridged_path`` (the caller assembles them first).
    - ``gap_report.json``, ``coverage.json``, ``review_state.json`` —
      ``model_dump_json(indent=2)`` of the given models. ``review_state.json``
      carries the per-section states and the banker edit audit trail.
    - ``contradictions.json``, ``examiner_objections.json``,
      ``arithmetic_findings.json``, ``generated_sections.json`` — list
      payloads wrapped as ``{"items": [...]}``.
    - ``facts_with_provenance.json`` — every fact in the store, unconfirmed
      and superseded included (see module docstring for why).
    - ``manifest.json`` — pinned regulation/schema version from the checklist
      header, the member list, and the draft-package note.
    """
    manifest = {
        "generated_by": "DRHP Studio",
        "regulation": checklist.header.regulation,
        "amended_through": checklist.header.amended_through,
        "schema_version": checklist.header.schema_version,
        "reviewed_by_human": checklist.header.reviewed_by_human,
        "contents": list(BUNDLE_MEMBERS),
        "note": BUNDLE_NOTE,
    }
    docx_members: dict[str, Path] = {
        "drhp.docx": drhp_path,
        "abridged.docx": abridged_path,
    }
    json_members: dict[str, str] = {
        "gap_report.json": gaps.model_dump_json(indent=2),
        "contradictions.json": _items_json(contradictions),
        "coverage.json": coverage.model_dump_json(indent=2),
        "examiner_objections.json": _items_json(objections),
        "arithmetic_findings.json": _items_json(arithmetic),
        "generated_sections.json": _items_json(sections),
        "facts_with_provenance.json": _items_json(store.all_facts()),
        "review_state.json": review_state.model_dump_json(indent=2),
        "manifest.json": json.dumps(manifest, indent=2, ensure_ascii=False),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for member in BUNDLE_MEMBERS:
                if member in docx_members:
                    archive.write(docx_members[member], arcname=member)
                else:
                    archive.writestr(member, json_members[member])
        tmp_path.replace(out_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return out_path
