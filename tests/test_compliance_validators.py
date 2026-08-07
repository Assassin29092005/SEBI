"""RPT cross-check, promoter lock-in, and pricing/valuation validators.

All three are deterministic (no LLM) and read only *confirmed* facts — an
unconfirmed proposal is not yet part of the draft, so flagging it would
raise objections against data the promoter has not vouched for.
"""

from __future__ import annotations

from app.facts import Fact, FactStore, Provenance, SourceKind
from app.schema.models import Role
from app.validate.lock_in import check_lock_in
from app.validate.pricing import check_pricing
from app.validate.rpt import check_rpt


def _fact(key: str, value: object, confirmed: bool = True) -> Fact:
    return Fact(
        key=key,
        value=value,
        provenance=Provenance(kind=SourceKind.WIZARD, detail="test"),
        confidence=0.9,
        confirmed=confirmed,
        supplied_by=Role.PROMOTER,
    )


def _store(*facts: Fact) -> FactStore:
    store = FactStore()
    for fact in facts:
        store.add(fact)
    return store


def _kinds(findings: list) -> set[str]:
    return {f.kind for f in findings}


# --------------------------------------------------------------------------
# RPT cross-check
# --------------------------------------------------------------------------


def test_rpt_entity_absent_from_promoter_group_is_flagged() -> None:
    findings = check_rpt(
        _store(
            _fact("related_party_names", ["Sunrise Holdings LLP", "Undisclosed Traders Pvt Ltd"]),
            _fact("promoter_group_entities", ["Sunrise Holdings LLP"]),
        )
    )
    flagged = [f for f in findings if f.kind == "rpt_entity_not_in_promoter_group"]
    assert [f.entity for f in flagged] == ["Undisclosed Traders Pvt Ltd"]
    assert flagged[0].severity == "material"


def test_rpt_entity_match_is_case_insensitive() -> None:
    findings = check_rpt(
        _store(
            _fact("related_party_names", ["SUNRISE HOLDINGS LLP"]),
            _fact("promoter_group_entities", ["Sunrise Holdings llp"]),
        )
    )
    assert "rpt_entity_not_in_promoter_group" not in _kinds(findings)


def test_rpt_amounts_without_materiality_policy_are_flagged() -> None:
    findings = check_rpt(
        _store(
            _fact("related_party_names", ["Sunrise Holdings LLP"]),
            _fact("promoter_group_entities", ["Sunrise Holdings LLP"]),
            _fact("rpt_total_amount_paise", 1_20_00_000),
        )
    )
    assert "rpt_missing_materiality" in _kinds(findings)


def test_rpt_materiality_policy_clears_the_flag() -> None:
    findings = check_rpt(
        _store(
            _fact("related_party_names", ["Sunrise Holdings LLP"]),
            _fact("rpt_total_amount_paise", 1_20_00_000),
            _fact("rpt_materiality_policy", "10% of consolidated turnover"),
        )
    )
    assert "rpt_missing_materiality" not in _kinds(findings)


def test_rpt_absent_altogether_prompts_a_nil_declaration() -> None:
    findings = check_rpt(_store(_fact("company_name", "Sunrise Agrotech Ltd")))
    assert _kinds(findings) == {"rpt_no_data"}


def test_rpt_ignores_unconfirmed_facts() -> None:
    """An unconfirmed RPT list must not satisfy the has-RPT-data check."""
    findings = check_rpt(_store(_fact("related_party_names", ["X Ltd"], confirmed=False)))
    assert "rpt_no_data" in _kinds(findings)


# --------------------------------------------------------------------------
# Promoter contribution + lock-in (ICDR Reg. 236–241)
# --------------------------------------------------------------------------


def test_promoter_contribution_below_twenty_percent_is_a_blocker() -> None:
    findings = check_lock_in(
        _store(
            _fact("issue_size_paise", 50_00_00_000),
            _fact("post_issue_capital_paise", 200_00_00_000),
            _fact("promoter_contribution_paise", 20_00_00_000),  # 10%
        )
    )
    breach = next(f for f in findings if f.kind == "insufficient_promoter_contribution")
    assert breach.severity == "blocker"
    assert "10%" in breach.detail


def test_promoter_contribution_at_the_threshold_passes() -> None:
    findings = check_lock_in(
        _store(
            _fact("issue_size_paise", 50_00_00_000),
            _fact("post_issue_capital_paise", 200_00_00_000),
            _fact("promoter_contribution_paise", 40_00_00_000),  # exactly 20%
        )
    )
    assert "promoter_contribution_ok" in _kinds(findings)
    assert "insufficient_promoter_contribution" not in _kinds(findings)


def test_contribution_is_checked_against_post_issue_capital_without_an_issue_size() -> None:
    """Reg. 236(1) is a percentage of post-issue capital, so that alone is
    enough to run the check.

    This gated on ``issue_size_paise`` being confirmed, even though the
    percentage was computed against post-issue capital — so a draft with
    both of the numbers the rule actually needs got no check at all, and a
    blocker silently never ran.
    """
    findings = check_lock_in(
        _store(
            _fact("post_issue_capital_paise", 200_00_00_000),
            _fact("promoter_contribution_paise", 20_00_00_000),  # 10%
        )
    )
    breach = next(f for f in findings if f.kind == "insufficient_promoter_contribution")
    assert breach.severity == "blocker"


def test_missing_promoter_contribution_is_flagged_once_issue_size_is_known() -> None:
    findings = check_lock_in(_store(_fact("issue_size_paise", 50_00_00_000)))
    assert "missing_promoter_contribution" in _kinds(findings)
    assert "missing_lock_in_disclosure" in _kinds(findings)


def test_stated_lock_in_below_three_years_is_a_blocker() -> None:
    findings = check_lock_in(
        _store(
            _fact("issue_size_paise", 50_00_00_000),
            _fact("promoter_lock_in_years", 1),
        )
    )
    breach = next(f for f in findings if f.kind == "insufficient_promoter_lock_in")
    assert breach.severity == "blocker"
    assert breach.clause_ref == "ICDR Reg. 238"


def test_lock_in_disclosure_present_suppresses_the_missing_flag() -> None:
    findings = check_lock_in(
        _store(
            _fact("issue_size_paise", 50_00_00_000),
            _fact("promoter_lock_in_years", 3),
        )
    )
    assert "missing_lock_in_disclosure" not in _kinds(findings)
    assert "insufficient_promoter_lock_in" not in _kinds(findings)


def test_no_facts_produces_no_lock_in_findings() -> None:
    """Nothing confirmed yet is not a violation — the gap report covers that."""
    assert check_lock_in(_store()) == []


# --------------------------------------------------------------------------
# Pricing / valuation (ICDR Sch. VI Part A, para (9)(K))
# --------------------------------------------------------------------------


def test_issue_price_below_face_value_is_a_blocker() -> None:
    findings = check_pricing(
        _store(_fact("face_value_paise", 1000), _fact("issue_price_paise", 500))
    )
    breach = next(f for f in findings if f.kind == "price_below_face_value")
    assert breach.severity == "blocker"


def test_price_band_spread_over_twenty_percent_is_flagged() -> None:
    findings = check_pricing(
        _store(_fact("price_band_low_paise", 10_000), _fact("price_band_high_paise", 13_000))
    )
    assert "excessive_price_band_spread" in _kinds(findings)


def test_price_band_spread_within_twenty_percent_passes() -> None:
    findings = check_pricing(
        _store(_fact("price_band_low_paise", 10_000), _fact("price_band_high_paise", 12_000))
    )
    assert "excessive_price_band_spread" not in _kinds(findings)


def test_stated_pe_inconsistent_with_price_over_eps_is_flagged() -> None:
    # ₹120.00 / ₹10.00 EPS = 12.0x, but the draft states 20x.
    findings = check_pricing(
        _store(
            _fact("issue_price_paise", 12_000),
            _fact("basic_eps", "10.00"),
            _fact("pe_ratio", "20.00"),
        )
    )
    mismatch = next(f for f in findings if f.kind == "pe_ratio_mismatch")
    assert "12.00x" in mismatch.detail


def test_stated_pe_matching_the_computation_passes() -> None:
    findings = check_pricing(
        _store(
            _fact("issue_price_paise", 12_000),
            _fact("basic_eps", "10.00"),
            _fact("pe_ratio", "12.00"),
        )
    )
    assert "pe_ratio_consistent" in _kinds(findings)


def test_large_premium_over_industry_pe_is_flagged() -> None:
    findings = check_pricing(
        _store(
            _fact("issue_price_paise", 12_000),  # PE 12x
            _fact("basic_eps", "10.00"),
            _fact("industry_pe_ratio", "6.00"),  # 100% premium
        )
    )
    assert "significant_pe_premium" in _kinds(findings)


def test_price_without_eps_is_flagged_as_missing_input() -> None:
    findings = check_pricing(_store(_fact("issue_price_paise", 12_000)))
    assert "missing_eps" in _kinds(findings)


def test_no_pricing_facts_produces_no_findings() -> None:
    assert check_pricing(_store()) == []
