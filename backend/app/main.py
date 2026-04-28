"""FastAPI app entry.

Boots the HTTP API, templates and SSE. The Arq worker runs as a separate
container but shares the same codebase.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

import logging

from app.db import init_db
from app.routers import cases, settings_api, stream, ui, visual

# Wave 1+2 modules: optional include, fail-soft so a missing dep at runtime
# (e.g. weasyprint, face_recognition) doesn't take down the whole API.
log = logging.getLogger(__name__)


def _try_include(app: FastAPI, importer, name: str) -> None:
    try:
        router = importer()
        app.include_router(router)
        log.info("router.included", extra={"router": name})
    except Exception as e:  # noqa: BLE001
        log.warning("router.skipped name=%s err=%s", name, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.observability.logging_setup import configure_logging
        configure_logging()
    except Exception:  # noqa: BLE001
        pass
    await init_db()
    yield


app = FastAPI(title="OSINT Tool", version="0.2.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(cases.router)
app.include_router(stream.router)
app.include_router(visual.router)
app.include_router(settings_api.router)
app.include_router(ui.router)


def _r_audit():
    from app.routers.audit_router import router as r
    return r


def _r_cache():
    from app.routers.cache_admin import router as r
    return r


def _r_entities():
    from app.routers.entities_router import router as r
    return r


def _r_graph():
    from app.routers.graph_router import router as r
    return r


def _r_graph_analytics():
    from app.routers.graph_analytics import router as r
    return r


def _r_metrics():
    from app.routers.metrics_router import router as r
    return r


def _r_pattern():
    from app.routers.pattern_miner_router import router as r
    return r


def _r_photos():
    from app.routers.photos_router import router as r
    return r


def _r_scoring():
    from app.routers.scoring_router import router as r
    return r


def _r_investigator():
    from app.routers.investigator_router import router as r
    return r


def _r_admin_keys():
    from app.security.admin_router import router as r
    return r


def _r_export():
    from app.routers.export_router import router as r
    return r


def _r_rules():
    from app.routers.rules_router import router as r
    return r


def _r_alerts():
    from app.routers.alerts_router import router as r
    return r


def _r_dlq():
    from app.routers.dlq_router import router as r
    return r


def _r_orgs():
    from app.routers.orgs_router import router as r
    return r


def _r_webhooks():
    from app.routers.webhooks_router import router as r
    return r


def _r_watchlist():
    from app.routers.watchlist_router import router as r
    return r


def _r_strava():
    from app.routers.strava import router as r
    return r


def _r_timeline():
    from app.routers.timeline_router import router as r
    return r


def _r_geo():
    from app.routers.geo_router import router as r
    return r


for _name, _imp in [
    ("audit", _r_audit),
    ("cache_admin", _r_cache),
    ("entities", _r_entities),
    ("graph", _r_graph),
    ("graph_analytics", _r_graph_analytics),
    ("metrics", _r_metrics),
    ("pattern_miner", _r_pattern),
    ("photos", _r_photos),
    ("scoring", _r_scoring),
    ("investigator", _r_investigator),
    ("admin_keys", _r_admin_keys),
    ("export", _r_export),
    ("rules", _r_rules),
    ("alerts", _r_alerts),
    ("dlq", _r_dlq),
    ("orgs", _r_orgs),
    ("webhooks", _r_webhooks),
    ("watchlist", _r_watchlist),
    ("strava", _r_strava),
    ("timeline", _r_timeline),
    ("geo", _r_geo),
]:
    _try_include(app, _imp, _name)


# Legacy /v2 redirect: /case/{id} is the canonical unified view.
from fastapi.responses import RedirectResponse  # noqa: E402


@app.get("/v2/case/{case_id}", include_in_schema=False)
@app.get("/v2/cases/{case_id}", include_in_schema=False)
def _v2_redirect(case_id: str):
    return RedirectResponse(url=f"/case/{case_id}", status_code=301)
