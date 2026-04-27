"""Prometheus /metrics endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

# Importing the metrics module ensures all collectors are registered.
from app.observability import metrics as _metrics  # noqa: F401

router = APIRouter(tags=["observability"])


@router.get("/metrics")
def metrics() -> Response:
    """Expose Prometheus metrics in text exposition format."""
    payload = generate_latest()
    # Pin the content type to the version Prometheus historically expects.
    return Response(content=payload, media_type="text/plain; version=0.0.4; charset=utf-8")


# WIRING (backend/app/main.py):
#   from app.routers.metrics_router import router as metrics_router
#   app.include_router(metrics_router)
