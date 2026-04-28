"""Concrete CollectorDispatcher used by the AI investigator.

Wave 3 — wires the previously-unimplemented Agent A8 seam directly to the
project's existing infrastructure:

* ``run_collector`` invokes a registered Collector against a synthetic
  SearchInput and persists every emitted Finding through the same path the
  orchestrator uses (``app.db.Finding`` + Redis publish).
* ``get_findings`` and ``get_entities`` read from the live DB.
* ``add_pivot`` not only registers the pivot in ``case_pivots`` (via
  ``app.pivot.maybe_dispatch``) — it also enqueues the collectors whose
  ``needs`` field includes the pivot kind, turning the LLM tool-use loop
  into a real auto-cascading investigation rather than a write-only log.
"""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

from sqlalchemy import select, text

from app.collectors.base import Collector, Finding as DataclassFinding
from app.db import Finding as FindingRow, session_scope
from app.event_bus import publish
from app.pivot.dispatcher import DispatchResult, maybe_dispatch
from app.pivot.extractor import Pivot
from app.pivot.policy import PIVOT_KINDS
from app.schemas import SearchInput

log = logging.getLogger(__name__)


class LiveCollectorDispatcher:
    """Production ``CollectorDispatcher`` (Protocol) implementation.

    The Protocol is duck-typed: see ``app.ai_investigator.runner`` for the
    expected method signatures. We do not import the Protocol here to keep
    this module standalone-testable.
    """

    async def run_collector(
        self,
        case_id: uuid.UUID,
        name: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        from app.collectors import collector_registry  # late import — avoids cycles

        cls = collector_registry.by_name(name)
        if cls is None:
            return {"error": f"unknown collector: {name}"}

        collector: Collector = cls()
        # Build a SearchInput from the provided dict. Unknown keys are dropped
        # by Pydantic so this is safe against drift.
        try:
            si = SearchInput(**{k: v for k, v in (inputs or {}).items() if v})
        except Exception as exc:  # noqa: BLE001
            return {"error": f"bad inputs: {exc}"}

        if not collector.applicable(si):
            return {"error": "collector not applicable for inputs", "collector": name}

        emitted: list[dict[str, Any]] = []
        try:
            async for f in collector.run(si):
                # Persist + publish so subsequent tool calls see the data.
                await self._persist_finding(case_id, f)
                emitted.append(
                    {
                        "collector": f.collector,
                        "category": f.category,
                        "entity_type": f.entity_type,
                        "title": f.title,
                        "url": f.url,
                        "confidence": f.confidence,
                        "payload": f.payload,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("investigator.run_collector failed name=%s err=%s", name, exc)
            return {"error": str(exc), "collector": name, "emitted": len(emitted)}
        return {"collector": name, "emitted": len(emitted), "findings": emitted[:25]}

    async def get_findings(
        self,
        case_id: uuid.UUID,
        filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        filt = filter or {}
        async with session_scope() as s:
            stmt = select(FindingRow).where(FindingRow.case_id == case_id)
            if "category" in filt:
                stmt = stmt.where(FindingRow.category == filt["category"])
            if "collector" in filt:
                stmt = stmt.where(FindingRow.collector == filt["collector"])
            if "entity_type" in filt:
                stmt = stmt.where(FindingRow.entity_type == filt["entity_type"])
            if "min_confidence" in filt:
                stmt = stmt.where(
                    FindingRow.confidence >= float(filt["min_confidence"])
                )
            stmt = stmt.order_by(FindingRow.confidence.desc()).limit(
                int(filt.get("limit") or 50)
            )
            rows = (await s.execute(stmt)).scalars().all()
        return [
            {
                "id": str(r.id),
                "collector": r.collector,
                "category": r.category,
                "entity_type": r.entity_type,
                "title": r.title,
                "url": r.url,
                "confidence": float(r.confidence or 0.0),
                "payload": r.payload,
            }
            for r in rows
        ]

    async def get_entities(self, case_id: uuid.UUID) -> dict[str, Any]:
        async with session_scope() as s:
            entity_rows = (
                await s.execute(
                    text(
                        "SELECT id, type, score, attrs FROM entities "
                        "WHERE case_id = :cid ORDER BY score DESC LIMIT 200"
                    ),
                    {"cid": str(case_id)},
                )
            ).mappings().all()
        return {
            "entities": [
                {
                    "id": str(r["id"]),
                    "type": r["type"],
                    "score": float(r["score"] or 0.0),
                    "attrs": r["attrs"],
                }
                for r in entity_rows
            ]
        }

    async def add_pivot(
        self,
        case_id: uuid.UUID,
        kind: str,
        value: str,
    ) -> dict[str, Any]:
        """Record the pivot AND dispatch collectors that target it.

        Wave 3: previously this method only persisted to ``case_pivots``;
        the LLM had no way to actually trigger downstream work. We now push
        the pivot through the same dispatcher the orchestrator uses, so
        ``add_pivot(kind="domain", value="example.com")`` causes
        ``dns_mx``/``rdap``/etc. to actually run.
        """
        kind = (kind or "").strip().lower()
        value = (value or "").strip()
        if kind not in PIVOT_KINDS:
            return {"error": f"unknown pivot kind: {kind}", "valid_kinds": sorted(PIVOT_KINDS)}
        if not value:
            return {"error": "value is empty"}
        try:
            pivot = Pivot(
                kind=kind, value=value, source_finding_id=None, confidence=0.95
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"invalid pivot: {exc}"}

        try:
            result: DispatchResult = await maybe_dispatch(case_id, [pivot], depth=0)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "investigator.add_pivot.dispatch_failed case=%s kind=%s err=%s",
                case_id,
                kind,
                exc,
            )
            return {"error": "dispatch_failed", "detail": str(exc)[:200]}

        return {
            "ok": True,
            "kind": kind,
            "value": value,
            "inserted": result.inserted,
            "enqueued_collectors": result.enqueued,
            "skipped": {
                "dedup": result.skipped_dedup,
                "depth": result.skipped_depth,
                "confidence": result.skipped_confidence,
                "budget": result.skipped_budget,
            },
        }

    # ------------------------------------------------------------------
    # Internal — finding persistence (mirrors orchestrator._run_one)
    # ------------------------------------------------------------------
    @staticmethod
    async def _persist_finding(case_id: uuid.UUID, f: DataclassFinding) -> None:
        fp = f.fingerprint()
        fid = uuid.uuid4()
        async with session_scope() as s:
            s.add(
                FindingRow(
                    id=fid,
                    case_id=case_id,
                    collector=f.collector,
                    category=f.category,
                    entity_type=f.entity_type,
                    title=(f.title or "")[:500],
                    url=f.url,
                    confidence=f.confidence,
                    payload=f.payload,
                    fingerprint=fp,
                )
            )
        try:
            await publish(
                case_id,
                {
                    "type": "finding",
                    "case_id": str(case_id),
                    "data": {
                        "id": str(fid),
                        "collector": f.collector,
                        "category": f.category,
                        "entity_type": f.entity_type,
                        "title": f.title,
                        "url": f.url,
                        "confidence": f.confidence,
                        "payload": f.payload,
                        "fingerprint": fp,
                        "source": "ai_investigator",
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("investigator.publish_failed: %s", exc)
