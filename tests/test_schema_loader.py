"""The schema is the trust foundation — these tests are the go/no-go gate."""

from app.schema.loader import load_checklist
from app.schema.models import Severity


def test_checklist_loads() -> None:
    checklist = load_checklist()
    assert checklist.entries, "checklist must not be empty"


def test_every_entry_cites_a_clause() -> None:
    checklist = load_checklist()
    for entry in checklist.entries:
        assert entry.clause_ref.strip(), f"{entry.id}: orphan requirement (no clause_ref)"


def test_header_pins_regulation_version() -> None:
    header = load_checklist().header
    assert header.amended_through
    assert "ICDR" in header.regulation


def test_non_stub_entries_declare_required_facts() -> None:
    checklist = load_checklist()
    for entry in checklist.entries:
        if not entry.stub:
            assert entry.required_facts, f"{entry.id}: non-stub entry with no required_facts"


def test_blockers_exist() -> None:
    # the certification lock is meaningless without blocker-severity entries
    assert any(e.severity == Severity.BLOCKER for e in load_checklist().entries)
