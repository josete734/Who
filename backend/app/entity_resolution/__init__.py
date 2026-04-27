"""Entity resolution & cross-collector dedup with confidence scoring.

This package reconciles raw Findings (one row per collector hit) into typed
Entities (Person, Account, Email, Phone, Domain, URL, Photo, Location,
Document) with confidence aggregated across sources.

Public API:
    - entities.Entity, EntitySource, EntityType
    - normalize.* — value normalizers
    - match.* — pairwise match rules
    - scoring.combine_confidences
    - engine.resolve(case_id) — async orchestration entry point
"""
from __future__ import annotations

from app.entity_resolution import entities, match, normalize, scoring

# `engine` imports SQLAlchemy/DB; import lazily to keep the unit-test surface
# usable in environments without DB drivers.
__all__ = ["entities", "match", "normalize", "scoring", "engine"]


def __getattr__(name: str):  # pragma: no cover - thin lazy loader
    if name == "engine":
        from app.entity_resolution import engine as _engine
        return _engine
    raise AttributeError(name)
