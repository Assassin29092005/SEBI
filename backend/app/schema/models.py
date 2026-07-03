"""Pydantic models for the checklist schema — the single source of truth.

No disclosure requirement lives in prompts or code; everything downstream
(wizard, generator, gap report, certification lock, coverage score) derives
from ``ChecklistEntry`` records loaded from the versioned YAML files.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Role(StrEnum):
    """Who can lawfully supply the content for a requirement."""

    PROMOTER = "promoter"
    AUDITOR = "auditor"
    BANKER = "banker"
    SYSTEM = "system"


class Severity(StrEnum):
    BLOCKER = "blocker"    # certification lock: export impossible until certified
    MATERIAL = "material"
    MINOR = "minor"


class OutputTarget(StrEnum):
    DRHP = "drhp"
    ABRIDGED = "abridged"


class ChecklistEntry(BaseModel):
    """One disclosure requirement from ICDR Chapter IX (via Schedule VI)."""

    id: str = Field(pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")
    clause_ref: str
    section: str
    title: str
    description: str
    applicability: str = "always"  # "always" or a named condition, e.g. "has_convertibles"
    required_facts: list[str]
    responsible_role: Role
    severity: Severity
    output_targets: list[OutputTarget]
    stub: bool = False  # entry exists for coverage accounting but is not yet fully encoded

    @field_validator("clause_ref")
    @classmethod
    def clause_ref_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("every checklist entry must cite its clause — no orphan requirements")
        return v


class ChecklistHeader(BaseModel):
    """Pins the exact regulation version the file encodes."""

    regulation: str            # e.g. "SEBI ICDR Regulations, 2018 — Chapter IX"
    amended_through: str       # ISO date of the last amendment encoded
    schema_version: str
    reviewed_by_human: bool    # every entry human-reviewed before shipping


class Checklist(BaseModel):
    header: ChecklistHeader
    entries: list[ChecklistEntry]

    def by_section(self) -> dict[str, list[ChecklistEntry]]:
        sections: dict[str, list[ChecklistEntry]] = {}
        for entry in self.entries:
            sections.setdefault(entry.section, []).append(entry)
        return sections

    def blockers(self) -> list[ChecklistEntry]:
        return [e for e in self.entries if e.severity == Severity.BLOCKER]
