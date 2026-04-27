"""PDF report exporter using WeasyPrint + Jinja2 templates."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "exports"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


async def _load_case_and_findings(case_id: uuid.UUID | str, db: Any) -> tuple[Any, list[Any]]:
    from sqlalchemy import select

    from app.db import Case, Finding  # local import to avoid cycles  # noqa: F401

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
    return case, findings


def _render_html(case: Any, findings: list[Any]) -> str:
    tpl = _env.get_template("report.html")
    enriched = []
    for f in findings:
        enriched.append({
            "entity_type": getattr(f, "entity_type", ""),
            "category": getattr(f, "category", ""),
            "title": getattr(f, "title", ""),
            "url": getattr(f, "url", None),
            "collector": getattr(f, "collector", ""),
            "confidence": float(getattr(f, "confidence", 0.0) or 0.0),
            "created_at": getattr(f, "created_at", ""),
            "payload_json": json.dumps(getattr(f, "payload", {}) or {}, indent=2, default=str),
        })
    return tpl.render(
        case=case,
        findings=enriched,
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )


async def export_pdf(case_id: uuid.UUID | str, db: Any) -> bytes:
    """Render the full case report as a PDF byte string."""
    case, findings = await _load_case_and_findings(case_id, db)
    html = _render_html(case, findings)

    # Imported lazily so unit tests can patch weasyprint without paying
    # the heavy native dependency cost during collection.
    from weasyprint import HTML  # type: ignore

    return HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf()
