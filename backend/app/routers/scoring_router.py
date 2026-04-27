"""Confidence scoring & explainability HTTP endpoints (Wave 2/B7).

# WIRING ----------------------------------------------------------------
# This router is intentionally NOT wired in main.py yet. Once Agent A3's
# scope-based auth lands and the entity store API stabilises, register it:
#
#       from app.routers.scoring_router import router as scoring_router
#       app.include_router(scoring_router)
#
# and replace the placeholder `_require_admin` below with the real
# dependency `Depends(require_scope("admin"))`.
# -----------------------------------------------------------------------

Endpoints:
    GET   /api/cases/{case_id}/entities/{eid}/explain  - per-entity breakdown
    GET   /api/scoring/quality                          - read collector table
    PATCH /api/scoring/quality                          - admin tune collector weights
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import session_scope
from app.scoring.engine import explain_entity
from app.scoring.model import ConfidenceExplanation
from app.scoring.quality import get_quality_table, set_many

router = APIRouter(tags=["scoring"])


# --------------------------------------------------------------------------
# Placeholder admin guard. Replace with real auth once A3 ships.
# --------------------------------------------------------------------------
async def _require_admin(request: Request) -> None:
    return None


# --------------------------------------------------------------------------
# /explain
# --------------------------------------------------------------------------
class _LiteSource:
    """Lightweight stand-in for EntitySource when reading from the DB."""

    __slots__ = ("collector", "confidence", "observed_at", "category")

    def __init__(self, collector: str, confidence: float, observed_at, category: str | None = None):
        self.collector = collector
        self.confidence = confidence
        self.observed_at = observed_at
        self.category = category


class _LiteEntity:
    __slots__ = ("type", "value", "attrs", "sources")

    def __init__(self, type_: str, value: str, attrs: dict[str, Any], sources: list[_LiteSource]):
        self.type = type_
        self.value = value
        self.attrs = attrs
        self.sources = sources


@router.get(
    "/api/cases/{case_id}/entities/{eid}/explain",
    response_model=ConfidenceExplanation,
)
async def explain(case_id: uuid.UUID, eid: uuid.UUID) -> ConfidenceExplanation:
    """Return a `ConfidenceExplanation` for one resolved entity."""
    async with session_scope() as s:
        node = (
            await s.execute(
                text(
                    "SELECT id, type, key, attrs FROM graph_nodes "
                    "WHERE id = :id AND case_id = :cid"
                ),
                {"id": str(eid), "cid": str(case_id)},
            )
        ).mappings().first()
        if node is None:
            raise HTTPException(status_code=404, detail="entity_not_found")

        # Pull source observations from findings linked by attrs.source_finding_ids
        # (best-effort; if absent we use category aggregation by collector).
        rows = (
            await s.execute(
                text(
                    "SELECT collector, category, confidence, created_at "
                    "FROM findings WHERE case_id = :cid"
                ),
                {"cid": str(case_id)},
            )
        ).mappings().all()

    src_ids = set((node["attrs"] or {}).get("source_finding_ids") or [])
    if src_ids:
        rows = [r for r in rows if str(r.get("id", "")) in src_ids]

    sources = [
        _LiteSource(
            collector=r["collector"],
            confidence=float(r["confidence"] or 0.0),
            observed_at=r["created_at"],
            category=r.get("category"),
        )
        for r in rows
    ]
    entity = _LiteEntity(
        type_=node["type"],
        value=node["key"],
        attrs=dict(node["attrs"] or {}),
        sources=sources,
    )
    qt = await get_quality_table()
    return explain_entity(entity, sources=sources, quality_table=qt)


# --------------------------------------------------------------------------
# /scoring/quality
# --------------------------------------------------------------------------
class QualityPatch(BaseModel):
    weights: dict[str, float] = Field(default_factory=dict)


@router.get("/api/scoring/quality")
async def get_quality() -> dict[str, float]:
    return await get_quality_table()


@router.patch("/api/scoring/quality")
async def patch_quality(
    body: QualityPatch,
    _: None = Depends(_require_admin),
) -> dict[str, float]:
    if not body.weights:
        raise HTTPException(status_code=400, detail="weights_required")
    for k, v in body.weights.items():
        if not isinstance(k, str) or not k:
            raise HTTPException(status_code=400, detail="invalid_collector_name")
        if not (0.0 <= float(v) <= 1.0):
            raise HTTPException(status_code=400, detail="weight_out_of_range")
    return await set_many(body.weights)
