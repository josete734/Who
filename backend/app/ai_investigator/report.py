"""Pydantic schema for the investigator's final report."""
from __future__ import annotations

from pydantic import BaseModel, Field


class InvestigatorReport(BaseModel):
    """Final structured report produced by the autonomous investigator."""

    summary: str = Field(..., description="Executive summary in the case's language.")
    key_entities: list[str] = Field(
        default_factory=list,
        description="Salient entities (people, accounts, domains).",
    )
    timeline_highlights: list[str] = Field(
        default_factory=list,
        description="Notable chronological events, oldest to newest.",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Known unknowns and avenues left unexplored.",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Suggested next steps for the human analyst.",
    )
    breakthrough_moments: list[str] = Field(
        default_factory=list,
        description=(
            "Pivots / collector calls that produced the largest jumps in "
            "confidence. Free-form, ordered chronologically."
        ),
    )
    dead_ends: list[str] = Field(
        default_factory=list,
        description=(
            "Collectors / pivots that returned no useful signal so the "
            "human analyst doesn't repeat them."
        ),
    )
    address_inferred: str | None = Field(
        default=None,
        description=(
            "Best human-readable address inferred for the subject (postal "
            "form). Use null when no defensible address could be derived."
        ),
    )
    primary_face_match: str | None = Field(
        default=None,
        description=(
            "Photo/cluster identifier representing the most reliable face "
            "match for the subject. Null if no face match was confirmed."
        ),
    )
    confidence_overall: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence in the synthesis [0,1].",
    )
