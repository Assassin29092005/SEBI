"""LLM cost & usage tracking: cost estimation, encrypted storage, filtering, summary."""

from __future__ import annotations

from pathlib import Path

from app.llm_usage import (
    LLM_USAGE_FILENAME,
    LlmUsageEvent,
    LlmUsageLog,
    estimate_cost_usd,
)


def _event(**overrides: object) -> LlmUsageEvent:
    defaults: dict[str, object] = {
        "feature": "generate_section",
        "provider": "gemini",
        "model": "gemini-2.0-flash",
        "input_tokens": 1000,
        "output_tokens": 500,
    }
    defaults.update(overrides)
    return LlmUsageEvent(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# estimate_cost_usd
# --------------------------------------------------------------------------


def test_estimate_cost_usd_known_model() -> None:
    # gemini-2.0-flash: $0.10/1M input, $0.40/1M output.
    cost = estimate_cost_usd("gemini", "gemini-2.0-flash", 1_000_000, 1_000_000)
    assert cost == 0.10 + 0.40


def test_estimate_cost_usd_unknown_model_returns_none() -> None:
    assert estimate_cost_usd("openai", "gpt-5", 1000, 1000) is None


def test_estimate_cost_usd_missing_token_counts_returns_none() -> None:
    assert estimate_cost_usd("gemini", "gemini-2.0-flash", None, 500) is None
    assert estimate_cost_usd("gemini", "gemini-2.0-flash", 500, None) is None


def test_estimate_cost_usd_zero_tokens_is_a_real_zero_not_none() -> None:
    assert estimate_cost_usd("gemini", "gemini-2.0-flash", 0, 0) == 0.0


# --------------------------------------------------------------------------
# Storage: encrypted, atomic, round-trips
# --------------------------------------------------------------------------


def test_record_then_reload_round_trips(tmp_path: Path) -> None:
    log = LlmUsageLog(directory=tmp_path)
    log.record(_event())

    reloaded = LlmUsageLog(directory=tmp_path)
    assert len(reloaded.list_events()) == 1
    assert reloaded.list_events()[0].feature == "generate_section"


def test_file_on_disk_is_encrypted_not_plaintext(tmp_path: Path) -> None:
    log = LlmUsageLog(directory=tmp_path)
    log.record(_event())

    raw = (tmp_path / LLM_USAGE_FILENAME).read_bytes()
    assert b"generate_section" not in raw
    assert b"gemini-2.0-flash" not in raw


def test_missing_directory_starts_empty(tmp_path: Path) -> None:
    log = LlmUsageLog(directory=tmp_path / "does_not_exist")
    assert log.list_events() == []
    assert log.summary().total_calls == 0


def test_corrupt_file_starts_empty_without_raising(tmp_path: Path) -> None:
    (tmp_path / LLM_USAGE_FILENAME).write_bytes(b"not encrypted, not json, just garbage")
    log = LlmUsageLog(directory=tmp_path)
    assert log.list_events() == []


# --------------------------------------------------------------------------
# list_events: filtering, ordering, limit
# --------------------------------------------------------------------------


def test_list_events_filters_by_feature_and_provider(tmp_path: Path) -> None:
    log = LlmUsageLog(directory=tmp_path)
    log.record(_event(feature="generate_section", provider="gemini"))
    log.record(_event(feature="extract_facts", provider="groq"))
    log.record(_event(feature="generate_section", provider="groq"))

    assert len(log.list_events(feature="generate_section")) == 2
    assert len(log.list_events(provider="groq")) == 2
    assert len(log.list_events(feature="generate_section", provider="groq")) == 1


def test_list_events_most_recent_first_and_respects_limit(tmp_path: Path) -> None:
    log = LlmUsageLog(directory=tmp_path)
    for i in range(5):
        log.record(_event(feature=f"call-{i}"))

    events = log.list_events(limit=2)
    assert len(events) == 2
    assert events[0].at >= events[1].at


# --------------------------------------------------------------------------
# summary(): aggregation, and the known/unknown-pricing distinction
# --------------------------------------------------------------------------


def test_summary_aggregates_totals_and_by_feature(tmp_path: Path) -> None:
    log = LlmUsageLog(directory=tmp_path)
    log.record(
        _event(
            feature="generate_section",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=estimate_cost_usd("gemini", "gemini-2.0-flash", 1000, 500),
        )
    )
    log.record(
        _event(
            feature="generate_section",
            input_tokens=2000,
            output_tokens=1000,
            cost_usd=estimate_cost_usd("gemini", "gemini-2.0-flash", 2000, 1000),
        )
    )
    log.record(
        _event(
            feature="extract_facts",
            provider="groq",
            model="llama-3.3-70b-versatile",
            input_tokens=500,
            output_tokens=200,
            cost_usd=estimate_cost_usd("groq", "llama-3.3-70b-versatile", 500, 200),
        )
    )

    summary = log.summary()
    assert summary.total_calls == 3
    assert summary.total_input_tokens == 3500
    assert summary.total_output_tokens == 1700
    assert summary.calls_with_unknown_pricing == 0
    assert summary.total_cost_usd is not None and summary.total_cost_usd > 0

    by_feature = {row.feature: row for row in summary.by_feature}
    assert by_feature["generate_section"].calls == 2
    assert by_feature["generate_section"].input_tokens == 3000
    assert by_feature["extract_facts"].calls == 1


def test_summary_distinguishes_unknown_pricing_from_zero_cost(tmp_path: Path) -> None:
    log = LlmUsageLog(directory=tmp_path)
    log.record(_event(provider="mystery", model="unlisted-model", cost_usd=None))

    summary = log.summary()
    assert summary.total_calls == 1
    assert summary.calls_with_unknown_pricing == 1
    # Nothing priced at all -> total is None, not a misleading $0.
    assert summary.total_cost_usd is None


def test_summary_on_empty_log_has_none_total_cost(tmp_path: Path) -> None:
    log = LlmUsageLog(directory=tmp_path)
    summary = log.summary()
    assert summary.total_calls == 0
    assert summary.total_cost_usd is None
    assert summary.by_feature == []
