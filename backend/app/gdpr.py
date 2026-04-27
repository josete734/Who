"""GDPR Art. 6 lawful-basis enforcement helpers.

# WIRING ----------------------------------------------------------------
# The cases router (backend/app/routers/cases.py) is owned by another
# agent. To enforce a mandatory legal basis at case creation, that
# router must add the lines below. Do NOT edit cases.py from this agent.
#
# 1. At the top of cases.py:
#
#       from app import audit
#       from app.gdpr import LegalBasis, validate_legal_basis
#
# 2. In the case-creation Pydantic payload (CaseCreate or similar):
#
#       legal_basis: LegalBasis  # required, enum-validated
#       legal_basis_note: str | None = None
#
# 3. In the create-case handler, immediately after parsing the payload:
#
#       validate_legal_basis(payload.legal_basis, payload.legal_basis_note)
#       # raises HTTPException(422, ...) on invalid input
#
#       case = Case(
#           title=payload.title,
#           legal_basis=payload.legal_basis.value,
#           legal_basis_note=payload.legal_basis_note,
#           input_payload=payload.input_payload,
#       )
#       session.add(case); await session.flush()
#
#       await audit.record(
#           "case.created",
#           case_id=case.id,
#           target={"title": case.title},
#           metadata={"legal_basis": case.legal_basis},
#           request=request,
#           actor_api_key_id=getattr(request.state, "api_key_id", None),
#       )
#
# 4. Register the audit router in main.py (owned by another agent):
#
#       from app.routers.audit_router import router as audit_router
#       app.include_router(audit_router)
# -----------------------------------------------------------------------
"""
from __future__ import annotations

from enum import Enum

from fastapi import HTTPException, status


class LegalBasis(str, Enum):
    """GDPR Art. 6(1) lawful bases for processing personal data."""

    CONSENT = "consent"
    LEGITIMATE_INTEREST = "legitimate_interest"
    LEGAL_OBLIGATION = "legal_obligation"
    PUBLIC_TASK = "public_task"
    VITAL_INTERESTS = "vital_interests"
    CONTRACT = "contract"

    @classmethod
    def values(cls) -> set[str]:
        return {m.value for m in cls}


# Bases that REQUIRE a written justification note (DPIA / record-of-processing
# reference). 'consent' and 'contract' are typically self-evident from the
# attached artefact; the others demand an explicit balancing test.
REQUIRES_NOTE: frozenset[str] = frozenset({
    LegalBasis.LEGITIMATE_INTEREST.value,
    LegalBasis.PUBLIC_TASK.value,
    LegalBasis.VITAL_INTERESTS.value,
    LegalBasis.LEGAL_OBLIGATION.value,
})


def validate_legal_basis(
    legal_basis: "LegalBasis | str | None",
    legal_basis_note: str | None,
) -> str:
    """Validate a legal-basis pair for case creation.

    Returns the canonical string value of the basis on success.
    Raises ``HTTPException(422)`` with a structured error otherwise.
    """
    if legal_basis is None or legal_basis == "":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "legal_basis_required",
                "message": (
                    "GDPR Art. 6 requires an explicit lawful basis for every "
                    "investigation. Provide one of: "
                    + ", ".join(sorted(LegalBasis.values()))
                ),
            },
        )

    value = legal_basis.value if isinstance(legal_basis, LegalBasis) else str(legal_basis)
    if value not in LegalBasis.values():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "legal_basis_invalid",
                "message": f"Unknown legal_basis '{value}'.",
                "allowed": sorted(LegalBasis.values()),
            },
        )

    if value in REQUIRES_NOTE and not (legal_basis_note and legal_basis_note.strip()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "legal_basis_note_required",
                "message": (
                    f"legal_basis '{value}' requires a written justification "
                    f"in 'legal_basis_note' (DPIA / balancing-test reference)."
                ),
            },
        )

    return value
