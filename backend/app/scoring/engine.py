"""Confidence explanation engine.

`explain_entity(entity, sources)` computes a final confidence score plus
a list of drivers and penalties that justify it. It is intentionally
deterministic and side-effect free so it can be called from any endpoint.

Inputs:
    entity   - either an `Entity` (from app.entity_resolution.entities) or
               any object exposing ``.type``, ``.value``, ``.attrs`` (the
               only attributes used here are ``attrs`` for conflict
               detection, plus ``type`` for context strings).
    sources  - iterable of `EntitySource`-like objects exposing
               ``collector``, ``confidence``, ``observed_at``.

Outputs:
    `ConfidenceExplanation` (see app.scoring.model).

Algorithm (high level):
    1. Each independent collector contributes one driver. Its weight is
       capped at the collector's quality * its self-reported confidence.
       The total driver contribution caps at MAX_INDEPENDENT_BONUS so a
       flood of identical-collector rows can't run away with the score.
    2. Corroboration: if observations span multiple finding categories
       (email + username + photo etc.) we add a small categorical bonus.
    3. Recency: sources observed within RECENT_WINDOW_DAYS get a slight
       multiplier; very stale sources are softly down-weighted.
    4. Penalties:
         - single-source-only            (-0.20)
         - low-quality collector only    (variable)
         - conflicting evidence in attrs (-0.25)
    5. Final score = clamp(noisy_or(weights) - sum(penalties), 0, 0.99).
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import Any

from app.scoring.model import ConfidenceExplanation, Driver, Penalty
from app.scoring.quality import (
    LOW_QUALITY_THRESHOLD,
    DEFAULT_QUALITY,
    is_low_quality,
    quality_for,
)

# Tunables -----------------------------------------------------------------

MAX_INDEPENDENT_BONUS = 0.95     # cap of summed driver weights from collectors
PER_COLLECTOR_CAP = 0.50         # any single collector contributes <=this
CORROBORATION_BONUS_2 = 0.10     # 2 distinct categories matching
CORROBORATION_BONUS_3 = 0.20     # 3+ distinct categories matching
RECENT_WINDOW_DAYS = 30
RECENCY_BONUS = 0.05
STALE_AFTER_DAYS = 365
STALE_PENALTY = 0.05

PENALTY_SINGLE_SOURCE = 0.20
PENALTY_LOW_QUALITY_ONLY = 0.15
PENALTY_CONFLICT = 0.25

CAP = 0.99


# --- helpers --------------------------------------------------------------

def _noisy_or(weights: Iterable[float]) -> float:
    prod = 1.0
    seen = False
    for w in weights:
        seen = True
        w = max(0.0, min(1.0, float(w)))
        prod *= 1.0 - w
    return 0.0 if not seen else 1.0 - prod


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _age_days(ts: dt.datetime | None) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return (_now() - ts).total_seconds() / 86400.0


def _categories_from_sources(sources: list[Any]) -> set[str]:
    """Best-effort: pull category-like signals from each source.

    We accept either an explicit ``category`` attr, or fall back to the
    collector name (e.g. "holehe" -> email). Used only for the
    corroboration bonus, so being slightly lossy is fine.
    """
    cats: set[str] = set()
    coll_to_cat = {
        "holehe": "email",
        "hibp": "email",
        "hunter": "email",
        "dehashed": "email",
        "sherlock": "username",
        "maigret": "username",
        "github": "username",
        "reddit": "username",
        "shodan": "host",
        "urlscan": "url",
        "leakix": "host",
        "wayback": "url",
        "companies_house": "company",
        "numverify": "phone",
    }
    for s in sources:
        c = getattr(s, "category", None)
        if c:
            cats.add(str(c))
            continue
        name = getattr(s, "collector", "") or ""
        cats.add(coll_to_cat.get(name, name or "unknown"))
    return cats


def _detect_conflicts(entity: Any) -> list[str]:
    """Detect contradictory evidence inside ``entity.attrs``.

    Looks for keys that map to a list/set of >1 distinct values which
    *should* be unique, e.g. multiple distinct ``real_name`` strings for
    the same Email entity.
    """
    conflicts: list[str] = []
    attrs = getattr(entity, "attrs", None) or {}
    unique_keys = ("real_name", "full_name", "owner", "dob", "country")
    for k in unique_keys:
        v = attrs.get(k)
        if isinstance(v, (list, tuple, set)):
            distinct = {str(x).strip().lower() for x in v if x}
            if len(distinct) > 1:
                conflicts.append(f"{k}: {sorted(distinct)}")
    # Also: nested {"conflicts": [...]}
    for c in attrs.get("conflicts") or []:
        conflicts.append(str(c))
    return conflicts


# --- main entry point -----------------------------------------------------

def explain_entity(
    entity: Any,
    sources: Iterable[Any] | None = None,
    quality_table: dict[str, float] | None = None,
) -> ConfidenceExplanation:
    """Build a `ConfidenceExplanation` for ``entity``.

    ``sources`` defaults to ``entity.sources`` if not provided. ``quality_table``
    defaults to the curated baseline; pass the merged DB-aware table for
    runtime-tuned weights.
    """
    qt = quality_table if quality_table is not None else DEFAULT_QUALITY
    src_list: list[Any] = list(sources) if sources is not None else list(getattr(entity, "sources", []) or [])

    drivers: list[Driver] = []
    penalties: list[Penalty] = []

    # ---- 1. per-collector drivers (capped) -----------------------------
    seen_collectors: set[str] = set()
    collector_weights: list[float] = []
    for s in src_list:
        name = getattr(s, "collector", "") or "unknown"
        if name in seen_collectors:
            continue  # only count *independent* collectors once
        seen_collectors.add(name)
        conf = float(getattr(s, "confidence", 0.5) or 0.5)
        q = quality_for(name, qt)
        w = min(PER_COLLECTOR_CAP, conf * q)
        collector_weights.append(w)
        drivers.append(
            Driver(
                source=name,
                weight=round(w, 4),
                reason=f"{name} reported with confidence {conf:.2f} (quality {q:.2f})",
            )
        )

    # Soft cap on aggregated collector contribution.
    raw_collector_score = _noisy_or(collector_weights)
    if raw_collector_score > MAX_INDEPENDENT_BONUS:
        raw_collector_score = MAX_INDEPENDENT_BONUS

    # ---- 2. corroboration across categories ----------------------------
    cats = _categories_from_sources(src_list)
    corrob_bonus = 0.0
    if len(cats) >= 3:
        corrob_bonus = CORROBORATION_BONUS_3
        drivers.append(
            Driver(
                source="corroboration",
                weight=corrob_bonus,
                reason=f"matches across {len(cats)} categories: {sorted(cats)}",
            )
        )
    elif len(cats) == 2:
        corrob_bonus = CORROBORATION_BONUS_2
        drivers.append(
            Driver(
                source="corroboration",
                weight=corrob_bonus,
                reason=f"matches across 2 categories: {sorted(cats)}",
            )
        )

    # ---- 3. recency ----------------------------------------------------
    recency_delta = 0.0
    ages = [a for a in (_age_days(getattr(s, "observed_at", None)) for s in src_list) if a is not None]
    if ages:
        min_age = min(ages)
        max_age = max(ages)
        if min_age <= RECENT_WINDOW_DAYS:
            recency_delta += RECENCY_BONUS
            drivers.append(
                Driver(
                    source="recency",
                    weight=RECENCY_BONUS,
                    reason=f"fresh observation within {RECENT_WINDOW_DAYS} days",
                )
            )
        if max_age >= STALE_AFTER_DAYS and min_age >= STALE_AFTER_DAYS:
            recency_delta -= STALE_PENALTY
            penalties.append(
                Penalty(
                    source="recency",
                    weight=STALE_PENALTY,
                    reason=f"all sources older than {STALE_AFTER_DAYS} days",
                )
            )

    # ---- 4. penalties --------------------------------------------------
    # 4a. single source
    if len(seen_collectors) <= 1:
        penalties.append(
            Penalty(
                source="single_source",
                weight=PENALTY_SINGLE_SOURCE,
                reason="only one independent collector reported this entity",
            )
        )

    # 4b. low-quality collectors only
    if seen_collectors and all(is_low_quality(n, qt) for n in seen_collectors):
        avg_q = sum(quality_for(n, qt) for n in seen_collectors) / len(seen_collectors)
        penalties.append(
            Penalty(
                source="low_quality_only",
                weight=PENALTY_LOW_QUALITY_ONLY,
                reason=(
                    f"all collectors below quality threshold "
                    f"{LOW_QUALITY_THRESHOLD:.2f} (avg {avg_q:.2f})"
                ),
            )
        )

    # 4c. conflicting evidence
    for conf_desc in _detect_conflicts(entity):
        penalties.append(
            Penalty(
                source="conflict",
                weight=PENALTY_CONFLICT,
                reason=f"conflicting evidence: {conf_desc}",
            )
        )

    # ---- 5. final score ------------------------------------------------
    score = raw_collector_score + corrob_bonus + recency_delta
    score -= sum(p.weight for p in penalties if p.source != "recency")
    # recency penalty was already folded into recency_delta above; avoid double-subtract
    score = max(0.0, min(CAP, score))

    return ConfidenceExplanation(
        score=round(score, 4),
        drivers=drivers,
        penalties=penalties,
    )
