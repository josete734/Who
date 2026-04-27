"""Pydantic models for confidence explanations.

A `ConfidenceExplanation` is what the API returns for a single resolved
entity / finding. It contains:

* ``score``     - aggregated confidence in [0, 1]; same scale as the engine's
                  noisy-OR combiner so UI can show a single number.
* ``drivers``   - positive contributions (each with weight + reason string).
* ``penalties`` - negative contributions (subtract from score, with reason).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Driver(BaseModel):
    """A positive signal raising confidence."""

    model_config = ConfigDict(extra="ignore")

    source: str  # collector name OR a synthetic key like "corroboration"
    weight: float = Field(ge=0.0, le=1.0)
    reason: str


class Penalty(BaseModel):
    """A negative signal lowering confidence."""

    model_config = ConfigDict(extra="ignore")

    source: str
    weight: float = Field(ge=0.0, le=1.0)
    reason: str


class ConfidenceExplanation(BaseModel):
    """Human-friendly breakdown of an entity's confidence score."""

    model_config = ConfigDict(extra="ignore")

    score: float = Field(ge=0.0, le=1.0)
    drivers: list[Driver] = Field(default_factory=list)
    penalties: list[Penalty] = Field(default_factory=list)
