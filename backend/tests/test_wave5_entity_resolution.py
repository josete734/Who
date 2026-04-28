"""Tests for Wave 5 — entity resolution improvements.

Covers:

* R11 ``organization_vat`` — same VAT across formatting variations merges.
* R12 ``event_temporal_geo`` — events within 24 h and 5 km merge; outside
  the window do not; spatial fallback to ``kind`` match.
* R13 homonym disambiguation in ``fuzzy_name`` — same name but different
  city / birth year / occupation no longer auto-merges.
* ``llm_tiebreaker`` — fail-soft on LLM error / unparseable response;
  correct routing when LLM returns a clean JSON verdict.
"""
from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock

import pytest

from app.entity_resolution.entities import Entity
from app.entity_resolution.llm_tiebreaker import (
    AMBIGUOUS_BAND,
    LLMTiebreakerResult,
    decide_same_person,
)
from app.entity_resolution.match import (
    ALL_RULES,
    event_temporal_geo,
    fuzzy_name,
    organization_vat,
)


def _person(name: str, **attrs):
    return Entity(
        id=uuid.uuid4(),
        type="Person",
        value=name,
        attrs=attrs,
        score=0.0,
    )


def _org(name: str, **attrs):
    return Entity(
        id=uuid.uuid4(),
        type="Organization",
        value=name,
        attrs=attrs,
        score=0.0,
    )


def _event(kind: str, **attrs):
    return Entity(
        id=uuid.uuid4(),
        type="Event",
        value=kind,
        attrs={"kind": kind, **attrs},
        score=0.0,
    )


# ---------------------------------------------------------------------------
# R11 — organization VAT
# ---------------------------------------------------------------------------
def test_org_vat_match_same_vat_different_format():
    a = _org("ACME SL", vat_id="ESB12345678")
    b = _org("ACME, S.L.", vat_id="B12345678")
    assert organization_vat(a, b) == 1.0


def test_org_vat_match_handles_punctuation():
    a = _org("ACME", vat_id="es-b 1234 5678")
    b = _org("ACME", vat_id="ESB12345678")
    assert organization_vat(a, b) == 1.0


def test_org_vat_no_match_different_vat():
    a = _org("ACME", vat_id="B12345678")
    b = _org("ACME", vat_id="B87654321")
    assert organization_vat(a, b) == 0.0


def test_org_vat_skips_non_org_entities():
    a = _person("ACME")
    b = _org("ACME", vat_id="B12345678")
    assert organization_vat(a, b) == 0.0


def test_org_vat_in_ALL_RULES():
    assert organization_vat in ALL_RULES


# ---------------------------------------------------------------------------
# R12 — event temporal+geo
# ---------------------------------------------------------------------------
def test_event_match_within_24h_and_5km():
    a = _event(
        "borme_appointment",
        when="2024-03-01T10:00:00+00:00",
        lat=41.3851,
        lon=2.1734,
    )
    b = _event(
        "borme_appointment",
        when="2024-03-01T18:00:00+00:00",
        lat=41.3900,
        lon=2.1800,  # ~700 m away
    )
    assert event_temporal_geo(a, b) == 0.85


def test_event_no_match_far_apart_in_time():
    a = _event(
        "borme_appointment",
        when="2024-03-01T10:00:00+00:00",
        lat=41.3851,
        lon=2.1734,
    )
    b = _event(
        "borme_appointment",
        when="2024-03-05T10:00:00+00:00",  # 4 days later
        lat=41.3851,
        lon=2.1734,
    )
    assert event_temporal_geo(a, b) == 0.0


def test_event_no_match_far_apart_in_space():
    a = _event(
        "borme_appointment",
        when="2024-03-01T10:00:00+00:00",
        lat=41.3851,
        lon=2.1734,
    )
    b = _event(
        "borme_appointment",
        when="2024-03-01T11:00:00+00:00",
        lat=40.4168,
        lon=-3.7038,  # Madrid, ~500 km away
    )
    assert event_temporal_geo(a, b) == 0.0


def test_event_temporal_only_match_when_kind_equal():
    a = _event("borme_appointment", when="2024-03-01T10:00:00+00:00")
    b = _event("borme_appointment", when="2024-03-01T15:00:00+00:00")
    # No coords on either side — fallback to temporal+kind match.
    assert event_temporal_geo(a, b) == 0.7


def test_event_no_match_different_kind_no_coords():
    a = _event("borme_appointment", when="2024-03-01T10:00:00+00:00")
    b = _event("boe_sanction", when="2024-03-01T10:30:00+00:00")
    assert event_temporal_geo(a, b) == 0.0


# ---------------------------------------------------------------------------
# R13 — homonym disambiguation in fuzzy_name
# ---------------------------------------------------------------------------
def test_fuzzy_name_same_city_boosts_above_baseline():
    # Without context, JW(Juan García, Juan García) = 1.0 → base 0.90.
    # With shared city, R13 adds +0.10 → 0.95.
    a = _person("Juan García", city="Madrid")
    b = _person("Juan García", city="Madrid")
    assert fuzzy_name(a, b) == pytest.approx(0.95)


def test_fuzzy_name_different_city_drops_to_zero_or_below_threshold():
    """Two Juan Garcías in different cities ARE different people. The R13
    malus of -0.20 brings the score down to 0.70 — still useable but no
    longer auto-merging at the typical engine threshold of 0.85+."""
    a = _person("Juan García", city="Madrid")
    b = _person("Juan García", city="Barcelona")
    score = fuzzy_name(a, b)
    assert score < 0.80, f"expected discordant city to lower score, got {score}"


def test_fuzzy_name_birth_year_match_within_two_years():
    a = _person("María Pérez", birth_year=1985)
    b = _person("Maria Perez", birth_year=1986)
    assert fuzzy_name(a, b) > 0.90  # bonus on top of the diacritic-folded match


def test_fuzzy_name_birth_year_far_apart_drops_score():
    a = _person("María Pérez", birth_year=1985)
    b = _person("María Pérez", birth_year=1955)
    assert fuzzy_name(a, b) < fuzzy_name(_person("María Pérez"), _person("María Pérez"))


def test_fuzzy_name_occupation_match_lifts_score():
    a = _person("Juan García", occupation="software engineer")
    b = _person("Juan García", occupation="software engineer")
    assert fuzzy_name(a, b) > 0.90


def test_fuzzy_name_occupation_conflict_lowers_score():
    a = _person("Juan García", occupation="doctor")
    b = _person("Juan García", occupation="lawyer")
    base = fuzzy_name(_person("Juan García"), _person("Juan García"))
    assert fuzzy_name(a, b) < base


def test_fuzzy_name_occupation_overlap_does_not_punish():
    """'software engineer' vs 'civil engineer' share 'engineer' — could be
    the same person mid-career change. Don't punish."""
    a = _person("Juan García", occupation="software engineer")
    b = _person("Juan García", occupation="civil engineer")
    base = fuzzy_name(_person("Juan García"), _person("Juan García"))
    # No bonus, no malus when occupations overlap.
    assert fuzzy_name(a, b) == pytest.approx(base, abs=0.01)


def test_fuzzy_name_no_attrs_no_bonus_no_malus():
    """Missing context = unknown; we don't reward or punish."""
    a = _person("Juan García")
    b = _person("Juan García")
    assert fuzzy_name(a, b) == pytest.approx(0.90)


# ---------------------------------------------------------------------------
# LLM tiebreaker
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tiebreaker_returns_no_merge_on_empty_cluster():
    out = await decide_same_person("Juan García", [], [{"title": "x"}])
    assert out.same_person is False
    assert out.reason == "empty_cluster"


@pytest.mark.asyncio
async def test_tiebreaker_parses_clean_llm_response(monkeypatch):
    """When the LLM returns a well-formed JSON decision, we surface it."""
    async def fake_llm_call(llm, prompt):
        return (
            '{"same_person": true, "confidence": 0.88, '
            '"reason": "Same Madrid software architect career."}',
            "gemini-2.5-pro-fake",
        )

    monkeypatch.setattr(
        "app.entity_resolution.llm_tiebreaker._llm_call",
        fake_llm_call,
        raising=False,
    )
    out = await decide_same_person(
        "Juan García",
        [{"title": "LinkedIn Madrid", "payload": {"city": "Madrid"}}],
        [{"title": "GitHub", "payload": {"city": "Madrid"}}],
    )
    assert out.same_person is True
    assert out.confidence == pytest.approx(0.88)
    assert "Madrid" in out.reason
    assert out.model == "gemini-2.5-pro-fake"


@pytest.mark.asyncio
async def test_tiebreaker_fails_closed_on_unparseable(monkeypatch):
    """Garbage from the LLM ⇒ default to NOT merge."""
    async def fake_llm_call(llm, prompt):
        return ("nonsense response with no json at all", "model-x")

    monkeypatch.setattr(
        "app.entity_resolution.llm_tiebreaker._llm_call",
        fake_llm_call,
        raising=False,
    )
    out = await decide_same_person("X", [{"title": "a"}], [{"title": "b"}])
    assert out.same_person is False
    assert out.reason == "parse_failed"
    assert out.model == "model-x"


@pytest.mark.asyncio
async def test_tiebreaker_fails_closed_on_llm_error(monkeypatch):
    async def fake_llm_call(llm, prompt):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "app.entity_resolution.llm_tiebreaker._llm_call",
        fake_llm_call,
        raising=False,
    )
    out = await decide_same_person("X", [{"title": "a"}], [{"title": "b"}])
    assert out.same_person is False
    assert "network down" in out.reason


def test_ambiguous_band_is_proper_interval():
    lo, hi = AMBIGUOUS_BAND
    assert 0.0 < lo < hi < 1.0
