"""Unit tests for the GDPR legal-basis enum and validator.

These tests are intentionally pure-Python and do NOT require a live
database; the audit writer itself is exercised via integration tests
elsewhere.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.gdpr import REQUIRES_NOTE, LegalBasis, validate_legal_basis


class TestLegalBasisEnum:
    def test_has_six_canonical_bases(self) -> None:
        assert LegalBasis.values() == {
            "consent",
            "legitimate_interest",
            "legal_obligation",
            "public_task",
            "vital_interests",
            "contract",
        }

    def test_str_value_round_trip(self) -> None:
        for member in LegalBasis:
            assert LegalBasis(member.value) is member

    def test_unknown_value_rejected(self) -> None:
        with pytest.raises(ValueError):
            LegalBasis("nope")


class TestValidateLegalBasis:
    def test_accepts_consent_without_note(self) -> None:
        assert validate_legal_basis(LegalBasis.CONSENT, None) == "consent"

    def test_accepts_contract_without_note(self) -> None:
        assert validate_legal_basis(LegalBasis.CONTRACT, "") == "contract"

    def test_accepts_string_value(self) -> None:
        assert validate_legal_basis("consent", None) == "consent"

    @pytest.mark.parametrize("missing", [None, ""])
    def test_rejects_missing_basis(self, missing: str | None) -> None:
        with pytest.raises(HTTPException) as exc:
            validate_legal_basis(missing, None)
        assert exc.value.status_code == 422
        assert exc.value.detail["error"] == "legal_basis_required"

    def test_rejects_unknown_basis(self) -> None:
        with pytest.raises(HTTPException) as exc:
            validate_legal_basis("telepathy", None)
        assert exc.value.status_code == 422
        assert exc.value.detail["error"] == "legal_basis_invalid"
        assert "telepathy" in exc.value.detail["message"]

    @pytest.mark.parametrize("basis", sorted(REQUIRES_NOTE))
    def test_requires_note_for_balancing_bases(self, basis: str) -> None:
        with pytest.raises(HTTPException) as exc:
            validate_legal_basis(basis, None)
        assert exc.value.status_code == 422
        assert exc.value.detail["error"] == "legal_basis_note_required"

    @pytest.mark.parametrize("basis", sorted(REQUIRES_NOTE))
    def test_requires_note_rejects_whitespace_only(self, basis: str) -> None:
        with pytest.raises(HTTPException):
            validate_legal_basis(basis, "   ")

    @pytest.mark.parametrize("basis", sorted(REQUIRES_NOTE))
    def test_accepts_note_when_required(self, basis: str) -> None:
        assert validate_legal_basis(basis, "DPIA-2026-04 ref #17") == basis
