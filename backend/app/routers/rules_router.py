"""CRUD + simulate endpoints for the rules engine.

# WIRING (NOT done in this PR) -----------------------------------------
#   from app.routers.rules_router import router as rules_router
#   app.include_router(rules_router)
# ----------------------------------------------------------------------
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import session_scope
from app.rules.dsl import Rule, evaluate_rule
from app.rules.engine import evaluate as engine_evaluate

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleIn(BaseModel):
    name: str = Field(..., max_length=255)
    dsl: dict[str, Any]
    enabled: bool = True


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    dsl: dict[str, Any]
    enabled: bool


class SimulateIn(BaseModel):
    event_kind: str
    event_payload: dict[str, Any] = Field(default_factory=dict)
    dsl: dict[str, Any] | None = None  # if absent, simulate against all enabled rules


@router.get("", response_model=list[RuleOut])
async def list_rules() -> list[RuleOut]:
    async with session_scope() as db:
        rows = (
            await db.execute(text("SELECT id, name, dsl, enabled FROM rules ORDER BY name"))
        ).mappings().all()
    return [RuleOut(id=r["id"], name=r["name"], dsl=r["dsl"] or {}, enabled=r["enabled"]) for r in rows]


@router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(payload: RuleIn) -> RuleOut:
    async with session_scope() as db:
        rid = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO rules (id, name, dsl, enabled) "
                "VALUES (:id, :name, CAST(:dsl AS JSONB), :enabled)"
            ),
            {"id": rid, "name": payload.name, "dsl": _json_dump(payload.dsl), "enabled": payload.enabled},
        )
        await db.commit()
    return RuleOut(id=rid, name=payload.name, dsl=payload.dsl, enabled=payload.enabled)


@router.put("/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: uuid.UUID, payload: RuleIn) -> RuleOut:
    async with session_scope() as db:
        result = await db.execute(
            text(
                "UPDATE rules SET name=:name, dsl=CAST(:dsl AS JSONB), enabled=:enabled "
                "WHERE id=:id"
            ),
            {"id": rule_id, "name": payload.name, "dsl": _json_dump(payload.dsl), "enabled": payload.enabled},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="rule not found")
        await db.commit()
    return RuleOut(id=rule_id, name=payload.name, dsl=payload.dsl, enabled=payload.enabled)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: uuid.UUID) -> None:
    async with session_scope() as db:
        result = await db.execute(text("DELETE FROM rules WHERE id=:id"), {"id": rule_id})
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="rule not found")
        await db.commit()


@router.post("/simulate")
async def simulate(payload: SimulateIn = Body(...)) -> dict[str, Any]:
    """Dry-run a DSL or all enabled rules against a synthetic event."""
    if payload.dsl is not None:
        rule = Rule(id=None, name="<simulated>", dsl=payload.dsl, enabled=True)
        alert = evaluate_rule(rule, payload.event_kind, payload.event_payload)
        return {
            "matched": alert is not None,
            "alert": _alert_dict(alert) if alert else None,
        }
    async with session_scope() as db:
        alerts = await engine_evaluate(payload.event_kind, payload.event_payload, db)
    return {"matched": len(alerts) > 0, "alerts": [_alert_dict(a) for a in alerts]}


def _alert_dict(a: Any) -> dict[str, Any]:
    return {
        "rule_id": a.rule_id,
        "rule_name": a.rule_name,
        "level": a.level,
        "message": a.message,
        "payload": a.payload,
        "case_id": a.case_id,
    }


def _json_dump(obj: Any) -> str:
    import json

    return json.dumps(obj)
