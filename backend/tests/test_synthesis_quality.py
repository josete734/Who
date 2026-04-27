"""Quality test for the post-case JSON synthesis.

We don't spin up Postgres here. Instead we patch:
- the LLM provider to return a deterministic JSON profile,
- ``session_scope`` and ``publish`` to no-op,
- the persistence helpers to capture what would be written.

The assertions verify that the produced profile dict carries every required
top-level field declared in :mod:`app.llm.prompts`.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest


REQUIRED_TOP_LEVEL = {
    "summary",
    "confirmed_identity",
    "digital_footprint",
    "breaches",
    "professional_signals",
    "personal_signals",
    "geographic_evidence",
    "risks",
    "gaps",
    "recommendations",
    "confidence_overall",
}


def _fake_profile() -> dict[str, Any]:
    return {
        "summary": "Subject identified as Jane Doe, Madrid-based DevOps engineer.",
        "confirmed_identity": {
            "name": "Jane Doe",
            "birth_name": None,
            "age_estimate": 34,
            "gender_estimate": "F",
            "photo_url": "https://example.com/jane.jpg",
            "location": {
                "city": "Madrid",
                "region": "Comunidad de Madrid",
                "country": "ES",
                "inferred_address": "Calle Gran Via 1, 28013 Madrid",
            },
            "primary_email": "jane@doe.example",
            "secondary_email": None,
        },
        "digital_footprint": [
            {"platform": "github", "url": "https://github.com/janedoe",
             "confidence": 0.92, "source_finding_id": "f-1"},
        ],
        "breaches": [
            {"source": "linkedin-2021", "date": "2021-06-22",
             "exposed_fields": ["email", "password_hash"],
             "source_finding_id": "f-7"},
        ],
        "professional_signals": [
            {"type": "employment", "value": "Acme SA - DevOps",
             "url": "https://linkedin.com/in/janedoe",
             "confidence": 0.85, "source_finding_id": "f-2"},
        ],
        "personal_signals": [
            {"category": "sport", "value": "trail running",
             "source_finding_id": "f-9"},
        ],
        "geographic_evidence": {
            "geo_signals": [
                {"lat": 40.41, "lon": -3.70, "label": "Madrid",
                 "source_finding_id": "f-3"},
            ],
            "inferred_locations": [
                {"city": "Madrid", "country": "ES",
                 "address": "Calle Gran Via 1, 28013 Madrid",
                 "confidence": 0.78,
                 "rationale": "EXIF + Strava cluster"},
            ],
        },
        "risks": [
            {"kind": "exposed_password",
             "description": "Password hash leaked in linkedin-2021",
             "severity": "high", "source_finding_id": "f-7"},
        ],
        "gaps": ["Phone number not confirmed"],
        "recommendations": ["Pivot on jane@doe.example via HIBP"],
        "confidence_overall": 0.81,
    }


def _make_findings(case_id: uuid.UUID, n: int = 20) -> list[Any]:
    findings = []
    for i in range(n):
        findings.append(
            SimpleNamespace(
                id=uuid.uuid4(),
                case_id=case_id,
                collector=f"col_{i % 5}",
                category=["username", "email", "phone", "name", "domain"][i % 5],
                entity_type=["Profile", "Breach", "Company", "Person"][i % 4],
                title=f"finding {i}",
                url=f"https://example.com/{i}",
                confidence=round(1.0 - i * 0.03, 2),
                payload={"k": i, "snippet": f"snippet {i}"},
                created_at=None,
            )
        )
    return findings


class _DummyResult:
    def __init__(self, items): self._items = items
    def scalar_one(self): return self._items[0]
    def scalars(self):
        outer = self
        class _S:
            def all(self_inner): return outer._items
        return _S()
    def mappings(self):
        outer = self
        class _M:
            def all(self_inner): return outer._items
        return _M()


class _DummySession:
    def __init__(self, case, findings):
        self._case = case
        self._findings = findings
        self.executed: list[Any] = []

    async def execute(self, stmt, params: dict | None = None):
        sql = str(stmt).lower()
        self.executed.append((sql, params))
        if "from cases" in sql:
            return _DummyResult([self._case])
        if "from findings" in sql:
            return _DummyResult(self._findings)
        # Aggregates / DDL / inserts: return empty mappings.
        return _DummyResult([])


@pytest.mark.asyncio
async def test_synthesize_produces_required_profile_fields(monkeypatch):
    from app.llm import synthesis as syn

    case_id = uuid.uuid4()
    case = SimpleNamespace(
        id=case_id,
        input_payload={"name": "Jane Doe", "country": "ES"},
    )
    findings = _make_findings(case_id, 20)
    dummy = _DummySession(case, findings)

    @asynccontextmanager
    async def _fake_session_scope():
        yield dummy

    captured: dict[str, Any] = {}

    async def _fake_persist(case_id, body, model, *, tokens_in=None, tokens_out=None):
        captured["body"] = body
        captured["model"] = model

    async def _fake_publish(*_a, **_kw):
        return None

    async def _fake_llm(prompt: str):  # noqa: ARG001
        return json.dumps(_fake_profile()), "test-model-1"

    monkeypatch.setattr(syn, "session_scope", _fake_session_scope)
    monkeypatch.setattr(syn, "publish", _fake_publish)
    monkeypatch.setattr(syn, "_persist_profile", _fake_persist)
    monkeypatch.setattr(syn, "_load_aggregates",
                        lambda _cid: _async_return({"inferred_locations": [],
                                                    "entities": {}, "photo_clusters": []}))
    monkeypatch.setattr(syn, "claude_generate", lambda p: _fake_llm(p))

    await syn.synthesize(case_id, "claude")

    assert "body" in captured, "profile was not persisted"
    body = captured["body"]
    missing = REQUIRED_TOP_LEVEL - set(body.keys())
    assert not missing, f"profile missing fields: {missing}"
    assert isinstance(body["confirmed_identity"], dict)
    assert isinstance(body["digital_footprint"], list)
    assert 0.0 <= float(body["confidence_overall"]) <= 1.0
    # Each digital_footprint entry must cite a source finding id.
    for entry in body["digital_footprint"]:
        assert "source_finding_id" in entry


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_synthesize_falls_back_to_markdown_on_invalid_json(monkeypatch):
    """If the LLM emits non-JSON, we still update the case with a Markdown dossier."""
    from app.llm import synthesis as syn

    case_id = uuid.uuid4()
    case = SimpleNamespace(id=case_id, input_payload={"name": "X"})
    findings = _make_findings(case_id, 20)
    dummy = _DummySession(case, findings)

    @asynccontextmanager
    async def _fake_session_scope():
        yield dummy

    async def _fake_publish(*_a, **_kw):
        return None

    calls: list[str] = []

    async def _fake_llm(prompt: str):  # noqa: ARG001
        calls.append(prompt[:30])
        # First call returns garbage, second (markdown fallback) returns text.
        if len(calls) == 1:
            return "this is not json at all", "m1"
        return "# Dossier\nbody", "m2"

    monkeypatch.setattr(syn, "session_scope", _fake_session_scope)
    monkeypatch.setattr(syn, "publish", _fake_publish)
    monkeypatch.setattr(syn, "_load_aggregates",
                        lambda _cid: _async_return({}))
    monkeypatch.setattr(syn, "claude_generate", lambda p: _fake_llm(p))

    await syn.synthesize(case_id, "claude")
    assert len(calls) == 2, "fallback markdown call was not made"
