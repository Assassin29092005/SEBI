"""Mock litigation connector: canned demo records, ontology alignment with wizard answers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import settings
from app.intake.litigation import LitigationRecord, MockLitigationConnector
from app.schema.loader import load_checklist
from app.schema.models import Role


def _run_search(entity: str) -> list[LitigationRecord]:
    connector = MockLitigationConnector()
    return asyncio.run(connector.search(entity, {}))


def test_search_for_demo_entity_returns_three_validated_records() -> None:
    records = _run_search("Sunrise Agrotech Ltd")

    assert len(records) == 3
    for record in records:
        assert isinstance(record, LitigationRecord)
        # every field populated per the model (nothing invented, nothing empty)
        assert record.case_number
        assert record.forum
        assert record.parties
        assert record.nature in {"civil", "criminal", "tax", "regulatory"}
        assert record.status

    # amounts, where present, are integer paise (never floats)
    for record in records:
        if record.amount_involved_paise is not None:
            assert isinstance(record.amount_involved_paise, int)
            assert record.amount_involved_paise > 0


def test_search_is_case_insensitive_on_the_entity_substring() -> None:
    lower = _run_search("sunrise agrotech ltd")
    upper = _run_search("SUNRISE AGROTECH LIMITED")
    assert len(lower) == 3
    assert len(upper) == 3


def test_search_for_unknown_entity_returns_empty_list() -> None:
    assert _run_search("Acme Widgets Ltd") == []
    assert _run_search("") == []


def test_wizard_answers_match_promoter_ontology_no_orphans() -> None:
    """Every promoter non-stub required_fact key is answered; no answers are orphaned.

    This is the contract the wizard/generator relies on: extraction and
    generation both key off the ontology, so drift here breaks the demo.
    """
    wizard_path: Path = settings.data_dir / "demo_company" / "wizard_answers.json"
    with wizard_path.open(encoding="utf-8") as fh:
        answers: dict[str, object] = json.load(fh)

    checklist = load_checklist()
    required: set[str] = set()
    for entry in checklist.entries:
        if not entry.stub and entry.responsible_role == Role.PROMOTER:
            required.update(entry.required_facts)

    answer_keys = set(answers.keys())
    missing = required - answer_keys
    orphan = answer_keys - required

    assert not missing, f"wizard_answers.json missing promoter facts: {sorted(missing)}"
    assert not orphan, f"wizard_answers.json has keys not in the ontology: {sorted(orphan)}"


def test_planted_contradiction_wizard_side_pins_the_expected_value() -> None:
    """The wizard side of the planted contradiction is Rs 12.5 crore in paise."""
    wizard_path: Path = settings.data_dir / "demo_company" / "wizard_answers.json"
    with wizard_path.open(encoding="utf-8") as fh:
        answers: dict[str, object] = json.load(fh)

    # Rs 12.5 crore = 12.5 * 1e7 rupees = 1.25e8 rupees = 1.25e10 paise
    assert answers["issue_size_paise"] == 12_500_000_000
