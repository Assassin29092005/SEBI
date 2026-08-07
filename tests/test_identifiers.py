"""Tests for PAN/CIN/GSTIN identifier format validators."""

from __future__ import annotations

from app.validate.identifiers import (
    IdentifierFinding,
    check_identifiers,
    validate_cin,
    validate_gstin,
    validate_pan,
)
from app.facts import Fact, FactStore, Provenance, SourceKind
from app.schema.models import Role


# --------------------------------------------------------------------------
# Unit tests: individual format validators
# --------------------------------------------------------------------------


def test_valid_pan_formats() -> None:
    """Standard valid PAN formats — 10 chars, 4th char is holder type."""
    assert validate_pan("ABCPD1234E") is True  # P = Person
    assert validate_pan("XYZCA9876B") is True  # C = Company
    assert validate_pan("MNOFH5678K") is True  # F = Firm
    assert validate_pan("abcpd1234e") is True  # case-insensitive


def test_invalid_pan_formats() -> None:
    """Common PAN format errors — length, wrong 4th char, mixed position errors."""
    assert validate_pan("ABCXD1234E") is False  # X is not a valid holder type
    assert validate_pan("ABC1D1234E") is False  # digit in first 3 positions
    assert validate_pan("ABCPD123E") is False   # too short (9 chars)
    assert validate_pan("ABCPD12345E") is False  # too long (11 chars)
    assert validate_pan("") is False
    assert validate_pan("12345") is False
    assert validate_pan("ABCPD1234") is False   # missing check letter


def test_valid_cin_formats() -> None:
    """Standard valid CIN formats — 21 chars."""
    # U = Unlisted, 74999 = NIC code, MH = Maharashtra, 2019 = year, PTC = ownership, 123456 = reg no
    assert validate_cin("U74999MH2019PTC123456") is True
    assert validate_cin("L01234DL2005PLC987654") is True  # L = Listed
    assert validate_cin("u74999mh2019ptc123456") is True  # case-insensitive


def test_invalid_cin_formats() -> None:
    """Common CIN format errors."""
    assert validate_cin("X74999MH2019PTC123456") is False  # must start U or L
    assert validate_cin("U7499MH2019PTC123456") is False   # NIC code too short
    assert validate_cin("U74999M2019PTC123456") is False   # state code too short
    assert validate_cin("U74999MH201PTC123456") is False   # year too short
    assert validate_cin("") is False
    assert validate_cin("RANDOM") is False


def test_valid_gstin_formats() -> None:
    """Standard valid GSTIN formats — 15 chars, ends with Z + check digit."""
    assert validate_gstin("27ABCPD1234EZ5") is False  # only 14 chars — wrong
    assert validate_gstin("27ABCPD1234E1Z5") is True  # correct 15 chars
    assert validate_gstin("07AAGCA1234B1ZQ") is True
    assert validate_gstin("29aagca1234b1zq") is True  # case-insensitive


def test_invalid_gstin_formats() -> None:
    """Common GSTIN format errors."""
    assert validate_gstin("XXABCPD1234E1Z5") is False  # state code must be digits
    assert validate_gstin("27ABCPD1234E1X5") is False  # missing Z at position 13
    assert validate_gstin("") is False
    assert validate_gstin("12345") is False


# --------------------------------------------------------------------------
# Integration tests: check_identifiers over a FactStore
# --------------------------------------------------------------------------

def _make_fact(key: str, value: str | list[str], confirmed: bool = True) -> Fact:
    """Helper to build a fact with the given key/value/confirmed status."""
    return Fact(
        key=key,
        value=value,
        provenance=Provenance(kind=SourceKind.WIZARD, detail="test"),
        confidence=0.9,
        confirmed=confirmed,
        supplied_by=Role.PROMOTER,
    )


def test_check_identifiers_valid() -> None:
    """Valid identifiers produce findings with valid=True."""
    store = FactStore()
    store.add(_make_fact("company_pan", "ABCCD1234E"))
    store.add(_make_fact("company_cin", "U74999MH2019PTC123456"))
    findings = check_identifiers(store)
    assert len(findings) == 2
    assert all(f.valid for f in findings)


def test_check_identifiers_invalid() -> None:
    """Invalid identifiers produce findings with valid=False."""
    store = FactStore()
    store.add(_make_fact("company_pan", "INVALID"))
    findings = check_identifiers(store)
    assert len(findings) == 1
    assert findings[0].valid is False
    assert "does not match" in findings[0].detail


def test_check_identifiers_skips_unconfirmed() -> None:
    """Unconfirmed facts are not checked — the promoter hasn't vouched yet."""
    store = FactStore()
    store.add(_make_fact("company_pan", "INVALID", confirmed=False))
    findings = check_identifiers(store)
    assert len(findings) == 0


def test_check_identifiers_list_values() -> None:
    """A fact with a list value validates each element separately."""
    store = FactStore()
    store.add(_make_fact("promoter_pan", ["ABCPD1234E", "NOTAPAN"]))
    findings = check_identifiers(store)
    assert len(findings) == 2
    valid_count = sum(1 for f in findings if f.valid)
    invalid_count = sum(1 for f in findings if not f.valid)
    assert valid_count == 1
    assert invalid_count == 1


def test_check_identifiers_ignores_unrelated_keys() -> None:
    """Facts with keys not in _IDENTIFIER_KEYS are skipped."""
    store = FactStore()
    store.add(_make_fact("company_name", "Sunrise Agrotech Ltd"))
    store.add(_make_fact("issue_size_paise", "50000000000"))
    findings = check_identifiers(store)
    assert len(findings) == 0
