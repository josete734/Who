"""Server-rendered HTML UI v2 (Wave 2 / B6).

This router is intentionally NOT wired into the FastAPI app yet — other agents
are still iterating. To enable, import and include in main.py:

# WIRING: from app.routers import ui_v2_router; app.include_router(ui_v2_router.router)
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(include_in_schema=False, tags=["ui-v2"])

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/v2/cases/{case_id}", response_class=HTMLResponse)
async def case_v2_page(case_id: str, request: Request):
    """Render the new visual single-page case experience.

    Consumed endpoints (built/maintained by sibling agents):
      - GET  /api/cases/{id}/graph            (B1)
      - GET  /api/cases/{id}/timeline
      - GET  /api/cases/{id}/photos/clusters
      - GET  /api/cases/{id}/geo/heatmap
      - GET  /api/cases/{id}/findings
      - POST /api/cases/{id}/investigate      (SSE stream)
    """
    # WIRING: register this router in app/main.py once B1..B5 endpoints land:
    # WIRING:     from app.routers import ui_v2_router
    # WIRING:     app.include_router(ui_v2_router.router)
    return templates.TemplateResponse(
        request,
        "case_v2.html",
        {"case_id": case_id},
    )
