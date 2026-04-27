"""Tests for the confidence scoring & explainability layer (Wave 2/B7).

Covers:
    * single-source-only penalty
    * cross-category corroboration driver
    * recency driver
    * conflicting-evidence penalty
    * low-quality-only penalty
    * admin weight tuning persists (via DB stub)
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from app.scoring import explain_entity
from app.scoring.engine import (
    PENALTY_CONFLICT,
    PENALTY_LOW_QUALITY_ONLY,
    PENALTY_SINGLE_SOURCE,
    RECENCY_BONUS,
)
from app.scoring.model import ConfidenceExplanation
from app.scoring import quality as quality_mod


# ---------- helpers --------------------------------------------------------

def _src(collector: str, confidence: float = 0.8, age_days: float = 1.0, category: str | None = None):
    ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age_days)
    return SimpleNamespace(collector=collector, confidence=confidence, observed_at=ts, category=category)


def _entity(attrs: dict | None = None, sources=None):
    return SimpleNamespace(type="Email", value="alice@example.com", attrs=attrs or {}, sources=list(sources or []))


# ---------- engine: drivers ------------------------------------------------

def test_single_source_triggers_penalty_and_low_score():
    e = _entity(sources=[_src("github", 0.9)])
    out = explain_entity(e)
    assert isinstance(out, ConfidenceExplanation)
    sources = {p.source for p in out.penalties}
    assert "single_source" in sources
    assert any(d.source == "github" for d in out.drivers)


def test_multi_collector_no_single_source_penalty():
    e = _entity(sources=[_src("github", 0.9), _src("hibp", 0.9), _src("holehe", 0.85)])
    out = explain_entity(e)
    assert "single_source" not in {p.source for p in out.penalties}
    # 3 distinct collectors -> reasonable score
    assert out.score > 0.5


def test_corroboration_bonus_three_categories():
    # email (holehe) + username (sherlock) + photo (manual w/ category)
    sources = [
        _src("holehe", 0.9),
        _src("sherlock", 0.9),
        _src("manual", 0.9, category="photo"),
    ]
    out = explain_entity(_entity(sources=sources))
    bonus_drivers = [d for d in out.drivers if d.source == "corroboration"]
    assert bonus_drivers, "expected a corroboration driver"
    assert bonus_drivers[0].weight >= 0.20  # CORROBORATION_BONUS_3


def test_corroboration_bonus_two_categories():
    sources = [_src("holehe", 0.9), _src("sherlock", 0.9)]
    out = explain_entity(_entity(sources=sources))
    bonus = [d for d in out.drivers if d.source == "corroboration"]
    assert bonus and 0.05 <= bonus[0].weight < 0.20


def test_recency_driver_present_for_fresh_source():
    out = explain_entity(_entity(sources=[_src("github", 0.9, age_days=1)]))
    assert any(d.source == "recency" and d.weight == RECENCY_BONUS for d in out.drivers)


def test_stale_only_sources_yield_recency_penalty():
    sources = [_src("github", 0.9, age_days=400), _src("hibp", 0.9, age_days=500)]
    out = explain_entity(_entity(sources=sources))
    assert any(p.source == "recency" for p in out.penalties)


# ---------- engine: penalties ----------------------------------------------

def test_conflicting_evidence_penalty():
    attrs = {"real_name": ["Alice Smith", "Bob Jones"]}
    sources = [_src("github", 0.9), _src("hibp", 0.9)]
    out = explain_entity(_entity(attrs=attrs, sources=sources))
    conflicts = [p for p in out.penalties if p.source == "conflict"]
    assert conflicts and conflicts[0].weight == PENALTY_CONFLICT


def test_no_conflict_when_unique_attribute_consistent():
    attrs = {"real_name": ["Alice Smith", "alice smith"]}  # casefold equal
    sources = [_src("github", 0.9), _src("hibp", 0.9)]
    out = explain_entity(_entity(attrs=attrs, sources=sources))
    assert not any(p.source == "conflict" for p in out.penalties)


def test_low_quality_only_penalty():
    # searxng (0.55) + google_dork (0.55) -> both <= LOW_QUALITY_THRESHOLD (0.60)
    sources = [_src("searxng", 0.7), _src("google_dork", 0.7)]
    out = explain_entity(_entity(sources=sources))
    assert any(p.source == "low_quality_only" and p.weight == PENALTY_LOW_QUALITY_ONLY for p in out.penalties)


def test_high_quality_collector_avoids_low_quality_penalty():
    sources = [_src("github", 0.9), _src("searxng", 0.7)]
    out = explain_entity(_entity(sources=sources))
    assert not any(p.source == "low_quality_only" for p in out.penalties)


# ---------- engine: scoring math -------------------------------------------

def test_single_source_score_lower_than_multi_source():
    one = explain_entity(_entity(sources=[_src("github", 0.9)]))
    many = explain_entity(_entity(sources=[_src("github", 0.9), _src("hibp", 0.9), _src("holehe", 0.85)]))
    assert many.score > one.score
    # single-source penalty is the dominant negative factor
    assert PENALTY_SINGLE_SOURCE in {p.weight for p in one.penalties}


def test_score_clamped_to_unit_interval():
    sources = [_src(f"github", 0.99) for _ in range(20)]  # repeated collector -> dedup
    out = explain_entity(_entity(sources=sources))
    assert 0.0 <= out.score <= 0.99


def test_independent_collectors_deduped():
    # 5 sherlock entries should count once (independence requirement)
    sources = [_src("sherlock", 0.8) for _ in range(5)]
    out = explain_entity(_entity(sources=sources))
    sherlocks = [d for d in out.drivers if d.source == "sherlock"]
    assert len(sherlocks) == 1


def test_quality_table_override_changes_score():
    sources = [_src("github", 0.9), _src("hibp", 0.9)]
    base = explain_entity(_entity(sources=sources))
    # halve github's quality
    table = dict(quality_mod.DEFAULT_QUALITY)
    table["github"] = 0.10
    tuned = explain_entity(_entity(sources=sources), quality_table=table)
    assert tuned.score < base.score


# ---------- quality persistence (DB stub) ----------------------------------

class _FakeSession:
    def __init__(self, store: dict[str, float]):
        self.store = store
        self.last_sql: str | None = None

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.last_sql = sql
        if "INSERT INTO collector_quality" in sql:
            self.store[params["n"]] = float(params["w"])
            return _FakeResult([])
        if "SELECT name, weight FROM collector_quality" in sql:
            return _FakeResult([(k, v) for k, v in self.store.items()])
        return _FakeResult([])


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSessionScope:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return _FakeSession(self.store)

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def fake_db(monkeypatch):
    store: dict[str, float] = {}

    def session_scope_stub():
        return _FakeSessionScope(store)

    monkeypatch.setattr(quality_mod, "session_scope", session_scope_stub)
    return store


async def test_set_collector_weight_persists(fake_db):
    out = await quality_mod.set_collector_weight("sherlock", 0.42)
    assert fake_db["sherlock"] == pytest.approx(0.42)
    # merged table reflects override
    assert out["sherlock"] == pytest.approx(0.42)
    # untouched collectors retain their default
    assert out["github"] == pytest.approx(quality_mod.DEFAULT_QUALITY["github"])


async def test_set_collector_weight_clamps(fake_db):
    await quality_mod.set_collector_weight("sherlock", 1.5)
    assert fake_db["sherlock"] == pytest.approx(1.0)
    await quality_mod.set_collector_weight("sherlock", -0.2)
    assert fake_db["sherlock"] == pytest.approx(0.0)


async def test_set_many_persists_all(fake_db):
    await quality_mod.set_many({"sherlock": 0.3, "maigret": 0.2})
    assert fake_db["sherlock"] == pytest.approx(0.3)
    assert fake_db["maigret"] == pytest.approx(0.2)


async def test_get_quality_table_falls_back_when_table_missing(monkeypatch):
    class _BoomScope:
        async def __aenter__(self):
            raise RuntimeError("no table")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(quality_mod, "session_scope", lambda: _BoomScope())
    out = await quality_mod.get_quality_table()
    # Falls back to DEFAULT_QUALITY entirely
    assert out["github"] == quality_mod.DEFAULT_QUALITY["github"]
