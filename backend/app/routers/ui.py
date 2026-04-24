"""Server-rendered HTML UI (Jinja2). No auth required."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings

router = APIRouter(include_in_schema=False)
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"domain": get_settings().public_domain},
    )


@router.get("/case/{case_id}", response_class=HTMLResponse)
async def case_page(case_id: str, request: Request):
    return templates.TemplateResponse(request, "case.html", {"case_id": case_id})


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {})
