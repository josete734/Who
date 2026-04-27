"""Tests for the secondary-email end-to-end support."""
from __future__ import annotations

from app.schemas import SearchInput


def test_emails_returns_both_in_order() -> None:
    inp = SearchInput(email="a@x.com", email_secondary="b@y.com")
    assert inp.emails() == ["a@x.com", "b@y.com"]


def test_emails_secondary_empty_returns_only_primary() -> None:
    inp = SearchInput(email="a@x.com", email_secondary=None)
    assert inp.emails() == ["a@x.com"]


def test_emails_dedup_when_duplicates() -> None:
    inp = SearchInput(email="a@x.com", email_secondary="A@x.com")
    out = inp.emails()
    # Case-insensitive dedup: only the primary survives.
    assert len(out) == 1
    assert out[0].lower() == "a@x.com"


def test_emails_only_secondary_set() -> None:
    inp = SearchInput(email=None, email_secondary="b@y.com")
    assert inp.emails() == ["b@y.com"]


def test_non_empty_fields_includes_email_secondary() -> None:
    inp = SearchInput(email="a@x.com", email_secondary="b@y.com")
    fields = inp.non_empty_fields()
    assert fields.get("email_secondary") == "b@y.com"


def test_pivot_extractor_marks_secondary_email() -> None:
    from types import SimpleNamespace

    from app.pivot.extractor import extract

    finding = SimpleNamespace(
        id="abc",
        title="t",
        url=None,
        confidence=0.9,
        payload={"email": "a@x.com", "email_secondary": "b@y.com"},
    )
    pivots = extract(finding)
    by_value = {p.value: p for p in pivots if p.kind == "email"}
    assert "a@x.com" in by_value
    assert "b@y.com" in by_value
    assert by_value["b@y.com"].evidence_dict.get("role") == "secondary"
    # Primary should not carry the secondary role tag.
    assert by_value["a@x.com"].evidence_dict.get("role") != "secondary"
