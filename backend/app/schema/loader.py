"""Loader and integrity checks for the checklist schema YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.schema.models import Checklist, ChecklistEntry, ChecklistHeader

CHECKLISTS_DIR = Path(__file__).parent / "checklists"


class SchemaIntegrityError(Exception):
    """The schema file violates a structural rule (see CLAUDE.md, 'The Checklist Schema')."""


def load_checklist(path: Path | None = None) -> Checklist:
    """Load and validate a checklist file. Defaults to the current pinned version."""
    if path is None:
        candidates = sorted(CHECKLISTS_DIR.glob("icdr_ch9_v*.yaml"))
        if not candidates:
            raise SchemaIntegrityError(f"no checklist files found in {CHECKLISTS_DIR}")
        path = candidates[-1]  # latest pinned version wins

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    header = ChecklistHeader.model_validate(raw["header"])
    entries = [ChecklistEntry.model_validate(e) for e in raw["entries"]]
    checklist = Checklist(header=header, entries=entries)
    _check_integrity(checklist)
    return checklist


def _check_integrity(checklist: Checklist) -> None:
    seen_ids: set[str] = set()
    for entry in checklist.entries:
        if entry.id in seen_ids:
            raise SchemaIntegrityError(f"duplicate entry id: {entry.id}")
        seen_ids.add(entry.id)
        if not entry.stub and not entry.required_facts:
            raise SchemaIntegrityError(
                f"{entry.id}: non-stub entry must declare required_facts "
                "(the wizard and generator are derived from them)"
            )
