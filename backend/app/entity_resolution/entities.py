"""Pydantic models for resolved entities."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal[
    "Person",
    "Account",
    "Email",
    "Phone",
    "Domain",
    "URL",
    "Photo",
    "Location",
    "Document",
    # Wave 5 — first-class organisations and events. ER rules R11 / R12 use
    # VAT-style identifiers and (when, where) tuples to dedupe across the
    # registry collectors (BORME, axesor, OpenCorporates, etc.).
    "Organization",
    "Event",
]


class EntitySource(BaseModel):
    """One observation that contributed to an Entity."""

    model_config = ConfigDict(extra="ignore")

    collector: str
    confidence: float = Field(ge=0.0, le=1.0)
    raw_finding_id: uuid.UUID | None = None
    observed_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class Entity(BaseModel):
    """A resolved, normalized OSINT entity."""

    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    case_id: uuid.UUID | None = None
    type: EntityType
    value: str  # canonical/normalized value (e.g. e164 phone, lowercased email)
    attrs: dict[str, Any] = Field(default_factory=dict)
    sources: list[EntitySource] = Field(default_factory=list)
    score: float = 0.0  # aggregated confidence (0..0.99)

    def add_source(self, src: EntitySource) -> None:
        self.sources.append(src)
