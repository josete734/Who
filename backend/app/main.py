"""FastAPI app entry.

Boots the HTTP API, templates and SSE. The Arq worker runs as a separate
container but shares the same codebase.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.db import init_db
from app.routers import cases, settings_api, stream, ui, visual


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="OSINT Tool", version="0.1.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(cases.router)
app.include_router(stream.router)
app.include_router(visual.router)
app.include_router(settings_api.router)
app.include_router(ui.router)
