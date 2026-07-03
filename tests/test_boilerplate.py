"""Boilerplate detector: filler phrases, 8-gram reference overlap, marker suppression."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.generate.sections import Citation, GeneratedSection, requires_input_marker
from app.validate import boilerplate
from app.validate.boilerplate import (
    GENERIC_FILLER_PHRASES,
    BoilerplateFlag,
    detect,
)


def _section(text: str, missing_facts: list[str] | None = None) -> GeneratedSection:
    return GeneratedSection(
        entry_id="business.overview",
        section="Our Business",
        text=text,
        citations=[Citation(fact_id="fact-1", text_span=(0, len(text)))] if text else [],
        missing_facts=missing_facts or [],
    )


# --- filler-phrase pass -----------------------------------------------------


def test_filler_phrase_is_flagged_with_exact_span(monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate this test from any real data/reference_drhps content on disk.
    monkeypatch.setattr(boilerplate, "REFERENCE_DRHPS_DIR", Path("nonexistent_dir_xyz"))
    text = "The Company operates a world-class manufacturing facility in Pune."
    section = _section(text)

    flags = detect(section)

    assert len(flags) == 1
    flag = flags[0]
    assert flag.reason == "generic filler"
    assert flag.entry_id == "business.overview"
    start, end = flag.text_span
    assert text[start:end].lower() == "world-class"


def test_filler_phrase_case_insensitive_matches_whole_word_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boilerplate, "REFERENCE_DRHPS_DIR", Path("nonexistent_dir_xyz"))
    # "Synergies" is in the list; "synergiesx" (embedded substring) must NOT match.
    text = "We expect Synergies from the acquisition. Note: synergiesx is a made-up word."
    section = _section(text)

    flags = detect(section)
    reasons = [f.reason for f in flags]
    assert reasons == ["generic filler"]
    start, end = flags[0].text_span
    assert text[start:end] == "Synergies"


def test_clean_disclosure_text_produces_no_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boilerplate, "REFERENCE_DRHPS_DIR", Path("nonexistent_dir_xyz"))
    text = (
        "The Company allotted 1,00,000 equity shares of face value Rs 10 each on 12 May 2019 "
        "to the Promoter for cash consideration."
    )
    section = _section(text)

    assert detect(section) == []


def test_filler_inside_requires_input_marker_is_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boilerplate, "REFERENCE_DRHPS_DIR", Path("nonexistent_dir_xyz"))
    # A fact key that literally contains a filler phrase substring: the marker
    # is honest-blank scaffolding, not disclosure prose — do not flag it.
    marker = requires_input_marker("world-class_status", "promoter")
    section = _section(f"Details pending. {marker}", missing_facts=["world-class_status"])

    assert detect(section) == []


def test_filler_outside_marker_is_flagged_even_when_marker_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boilerplate, "REFERENCE_DRHPS_DIR", Path("nonexistent_dir_xyz"))
    marker = requires_input_marker("fy2024_revenue", "auditor")
    text = f"The Company is a world-class operator. {marker}"
    section = _section(text, missing_facts=["fy2024_revenue"])

    flags = detect(section)
    assert len(flags) == 1
    assert flags[0].reason == "generic filler"
    start, end = flags[0].text_span
    assert text[start:end] == "world-class"


def test_generic_filler_list_has_expected_size() -> None:
    # Guardrail: the constant list is the module contract.
    assert len(GENERIC_FILLER_PHRASES) == 15
    assert "world-class" in GENERIC_FILLER_PHRASES


# --- 8-gram overlap pass ----------------------------------------------------


def test_eight_gram_overlap_flags_shared_phrasing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = tmp_path / "refs"
    reference.mkdir()
    # This reference "DRHP" contains a distinctive 8-word phrase.
    (reference / "sample.txt").write_text(
        "The company was incorporated under the Companies Act, 2013 in the State of Maharashtra.",
        encoding="utf-8",
    )
    monkeypatch.setattr(boilerplate, "REFERENCE_DRHPS_DIR", reference)

    # The section text embeds the same 8+ word phrase verbatim.
    text = (
        "As stated in our filings, the company was incorporated under the Companies Act, 2013 "
        "in the State of Maharashtra and has grown since."
    )
    section = _section(text)

    flags = detect(section)
    reasons = {f.reason for f in flags}
    assert "near-duplicate of reference DRHP" in reasons

    # Every near-duplicate flag's span must lie inside the shared phrase
    # region of the section text. The shared 8-gram windows slide across the
    # phrase, so each individual span is only part of the full phrase — but
    # every span must be fully contained within the shared region.
    shared_start = text.index("the company was incorporated")
    shared_end = text.index("Maharashtra") + len("Maharashtra")
    near_dup_flags = [
        f for f in flags if f.reason == "near-duplicate of reference DRHP"
    ]
    assert near_dup_flags, "expected at least one near-duplicate flag"
    for flag in near_dup_flags:
        start, end = flag.text_span
        assert shared_start <= start and end <= shared_end


def test_eight_gram_overlap_inside_marker_is_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = tmp_path / "refs"
    reference.mkdir()
    (reference / "sample.txt").write_text(
        "alpha beta gamma delta epsilon zeta eta theta iota", encoding="utf-8"
    )
    monkeypatch.setattr(boilerplate, "REFERENCE_DRHPS_DIR", reference)

    # Note: fact keys inside a marker aren't disclosure prose. Wrapping the
    # matching 8-gram entirely inside a REQUIRES INPUT marker must suppress it.
    marker = "[REQUIRES INPUT: alpha beta gamma delta epsilon zeta eta theta — promoter can provide this]"  # noqa: E501
    section = _section(marker, missing_facts=["alpha beta gamma delta epsilon zeta eta theta"])

    flags = [f for f in detect(section) if f.reason == "near-duplicate of reference DRHP"]
    assert flags == []


def test_missing_reference_dir_silently_no_ops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boilerplate, "REFERENCE_DRHPS_DIR", Path("does_not_exist_1234"))
    text = "Ordinary disclosure prose about the company operations."
    section = _section(text)

    # No filler, no reference corpus → zero flags, no exception.
    assert detect(section) == []


def test_empty_reference_dir_silently_no_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty_refs"
    empty.mkdir()
    monkeypatch.setattr(boilerplate, "REFERENCE_DRHPS_DIR", empty)
    section = _section("Ordinary disclosure prose about the company operations.")

    assert detect(section) == []


def test_flag_is_boilerplate_flag_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boilerplate, "REFERENCE_DRHPS_DIR", Path("nonexistent_dir_xyz"))
    section = _section("We deliver a seamless experience to customers.")

    flags = detect(section)
    assert len(flags) == 1
    assert isinstance(flags[0], BoilerplateFlag)
