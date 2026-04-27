"""MISP event JSON exporter."""
from __future__ import annotations

import uuid
from typing import Any

# Heuristic mapping from finding category to MISP attribute type.
_CATEGORY_TO_MISP: dict[str, tuple[str, str]] = {
    "email": ("email-src", "Network activity"),
    "username": ("text", "Social network"),
    "phone": ("phone-number", "Other"),
    "name": ("text", "Person"),
    "domain": ("domain", "Network activity"),
    "url": ("url", "Network activity"),
    "ip": ("ip-src", "Network activity"),
}


def _attribute_for(finding: Any) -> dict:
    cat = (getattr(finding, "category", "") or "").lower()
    misp_type, misp_category = _CATEGORY_TO_MISP.get(cat, ("text", "Other"))
    value = getattr(finding, "url", None) or getattr(finding, "title", "") or ""
    return {
        "type": misp_type,
        "category": misp_category,
        "to_ids": False,
        "value": str(value),
        "comment": f"{getattr(finding, 'collector', '')}/{getattr(finding, 'entity_type', '')}",
    }


async def export_misp(case_id: uuid.UUID | str, db: Any) -> dict:
    """Build a MISP event JSON document for a case."""
    from sqlalchemy import select

    from app.db import Case, Finding  # noqa: F401

    if isinstance(case_id, str):
        try:
            case_id = uuid.UUID(case_id)
        except ValueError:
            pass

    case = await db.get(Case, case_id)
    if case is None:
        raise ValueError(f"case {case_id} not found")

    res = await db.execute(
        select(Finding).where(Finding.case_id == case_id).order_by(Finding.created_at.asc())
    )
    findings = list(res.scalars().all())

    attributes = [_attribute_for(f) for f in findings]

    event = {
        "Event": {
            "uuid": str(case.id),
            "info": f"OSINT case: {case.title}",
            "analysis": "2",  # completed
            "threat_level_id": "4",  # undefined
            "distribution": "0",  # your org only
            "date": str(getattr(case, "created_at", "") or "")[:10],
            "Attribute": attributes,
            "Tag": [
                {"name": f"osint:legal-basis=\"{case.legal_basis or 'unspecified'}\""},
                {"name": "tlp:amber"},
            ],
        }
    }
    return event
