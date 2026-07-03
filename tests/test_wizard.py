"""Wizard question-flow tests: multilingual copy, input hints, and safe fallback.

The wizard is derived from the checklist schema; UX copy lives in
``question_copy.yaml`` alongside the wizard. These tests pin the demo-grade
guarantees: every promoter-role, non-stub required-fact key ships with EN + HI
copy, the Hindi path actually returns Hindi, input-hint heuristics work, and
missing copy never breaks the wizard.
"""

from __future__ import annotations

from app.intake.wizard import derive_questions
from app.schema.loader import load_checklist
from app.schema.models import (
    Checklist,
    ChecklistEntry,
    ChecklistHeader,
    OutputTarget,
    Role,
    Severity,
)


def _promoter_keys(checklist: Checklist) -> set[str]:
    return {
        key
        for entry in checklist.entries
        if not entry.stub and entry.responsible_role == Role.PROMOTER
        for key in entry.required_facts
    }


def test_every_promoter_key_has_en_and_hi_copy() -> None:
    """The demo promises a multilingual wizard — no promoter question is orphaned."""
    checklist = load_checklist()
    questions_en = derive_questions(checklist, lang="en")
    questions_hi = derive_questions(checklist, lang="hi")

    keys = _promoter_keys(checklist)
    en_prompts = {q.fact_key: q.prompt for q in questions_en}
    hi_prompts = {q.fact_key: q.prompt for q in questions_hi}

    for key in keys:
        assert key in en_prompts, f"missing EN copy for {key}"
        assert key in hi_prompts, f"missing HI copy for {key}"
        # A missing YAML entry silently falls back to the humanised English
        # prompt ("Please provide: ..."). If that string leaks through, copy
        # is missing — the guarantee is broken.
        assert not en_prompts[key].startswith("Please provide:"), (
            f"EN copy missing / fell back for {key}"
        )
        assert not hi_prompts[key].startswith("Please provide:"), (
            f"HI copy missing / fell back for {key}"
        )


def test_hindi_prompts_are_non_empty_and_differ_from_english() -> None:
    checklist = load_checklist()
    en_by_key = {q.fact_key: q.prompt for q in derive_questions(checklist, lang="en")}
    hi_by_key = {q.fact_key: q.prompt for q in derive_questions(checklist, lang="hi")}

    for key, hi_prompt in hi_by_key.items():
        assert hi_prompt.strip(), f"empty HI prompt for {key}"
        assert hi_prompt != en_by_key[key], f"HI prompt equals EN for {key}"


def _fabricate_checklist(fact_key: str) -> Checklist:
    entry = ChecklistEntry(
        id="capital_structure.share_capital_history",
        clause_ref="ICDR Sch. VI Part A (as applied by Ch. IX), para 9(A)",
        section="Capital Structure",
        title="History of equity share capital",
        description="Build-up of share capital since incorporation.",
        required_facts=[fact_key],
        responsible_role=Role.PROMOTER,
        severity=Severity.BLOCKER,
        output_targets=[OutputTarget.DRHP],
    )
    return Checklist(
        header=ChecklistHeader(
            regulation="SEBI ICDR Regulations, 2018 — Chapter IX",
            amended_through="2026-03-21",
            schema_version="test",
            reviewed_by_human=True,
        ),
        entries=[entry],
    )


def test_input_hint_money_for_paise_key() -> None:
    checklist = _fabricate_checklist("issue_size_paise")
    (question,) = derive_questions(checklist, lang="en")
    assert question.input_hint == "money"


def test_input_hint_list_for_bracket_key() -> None:
    checklist = _fabricate_checklist("kmp[]")
    (question,) = derive_questions(checklist, lang="en")
    assert question.input_hint == "list"


def test_input_hint_text_for_plain_key() -> None:
    checklist = _fabricate_checklist("business_description")
    (question,) = derive_questions(checklist, lang="en")
    assert question.input_hint == "text"


def test_input_hint_date_for_date_key() -> None:
    # Not a real schema key, but the hint rule must still fire.
    checklist = _fabricate_checklist("incorporation_date")
    (question,) = derive_questions(checklist, lang="en")
    assert question.input_hint == "date"


def test_fallback_prompt_for_unknown_fact_key_never_raises() -> None:
    """A fabricated key with no copy must still yield a usable question."""
    checklist = _fabricate_checklist("fake_key_xyz")
    (question,) = derive_questions(checklist, lang="hi")
    assert question.prompt == "Please provide: fake key xyz"
    # The schema still owns clause + description, so those pass through:
    assert question.clause_ref.startswith("ICDR")
    assert question.why_we_ask == "Build-up of share capital since incorporation."


def test_unknown_language_falls_back_to_english() -> None:
    checklist = _fabricate_checklist("business_description")
    en = derive_questions(checklist, lang="en")[0].prompt
    fallback = derive_questions(checklist, lang="fr")[0].prompt
    assert fallback == en


def test_derive_questions_default_lang_is_english() -> None:
    checklist = load_checklist()
    default = derive_questions(checklist)
    english = derive_questions(checklist, lang="en")
    assert [q.prompt for q in default] == [q.prompt for q in english]


def test_stub_and_non_promoter_entries_are_skipped() -> None:
    checklist = load_checklist()
    questions = derive_questions(checklist)
    emitted_ids = {q.checklist_entry_id for q in questions}
    for entry in checklist.entries:
        if entry.stub or entry.responsible_role != Role.PROMOTER:
            assert entry.id not in emitted_ids, (
                f"non-promoter or stub entry {entry.id} leaked into the wizard"
            )
