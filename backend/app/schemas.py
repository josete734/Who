from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator


InputType = Literal["email", "phone", "username", "name", "linkedin", "domain", "image"]


class SearchInput(BaseModel):
    """Free-form input from the user. At least one field required."""

    model_config = ConfigDict(extra="ignore")

    full_name: str | None = None
    birth_name: str | None = None     # nombre legal completo (de pila) si difiere del habitual
    aliases: str | None = None        # variantes separadas por comas
    email: EmailStr | None = None
    phone: str | None = None  # E.164 preferred: +34xxx
    username: str | None = None
    linkedin_url: str | None = None
    domain: str | None = None
    city: str | None = None
    country: str | None = None
    extra_context: str | None = None

    @field_validator("full_name", "birth_name", "aliases", "username", "phone", "linkedin_url", "domain", "city", "country", "extra_context", mode="before")
    @classmethod
    def strip_blank(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    def non_empty_fields(self) -> dict[str, Any]:
        d = self.model_dump(exclude_none=True)
        return {k: v for k, v in d.items() if v not in (None, "")}

    def name_variants(self) -> list[str]:
        """All name forms to try when searching: full_name, birth_name, each alias."""
        seen: set[str] = set()
        out: list[str] = []
        for n in (self.full_name, self.birth_name):
            if n and n.lower() not in seen:
                seen.add(n.lower()); out.append(n)
        if self.aliases:
            for part in self.aliases.split(","):
                p = part.strip()
                if p and p.lower() not in seen:
                    seen.add(p.lower()); out.append(p)
        return out


class NewCaseRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    legal_basis: str = Field(default="Investigación personal lícita bajo interés legítimo (art. 6.1.f RGPD)")
    input: SearchInput
    llm: Literal["gemini", "openai", "ollama", "claude", "none"] = "gemini"
    timeout_minutes: int = Field(default=20, ge=1, le=180)


class CaseOut(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    legal_basis: str
    input_payload: dict
    synthesis_markdown: str | None = None
    synthesis_model: str | None = None
    created_at: dt.datetime
    finished_at: dt.datetime | None = None
    error: str | None = None


class FindingOut(BaseModel):
    id: uuid.UUID
    collector: str
    category: str
    entity_type: str
    title: str
    url: str | None = None
    confidence: float
    payload: dict
    created_at: dt.datetime


class CollectorRunOut(BaseModel):
    collector: str
    status: str
    findings_count: int
    duration_ms: int | None = None
    message: str | None = None


class StreamEvent(BaseModel):
    """Generic SSE event emitted during a case run."""

    type: Literal["finding", "collector_start", "collector_end", "synthesis", "done", "error", "heartbeat"]
    case_id: uuid.UUID | None = None
    data: dict = Field(default_factory=dict)
    ts: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
