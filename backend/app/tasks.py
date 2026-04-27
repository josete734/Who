"""Arq worker: receives the orchestration job from the FastAPI app."""
from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from arq.connections import RedisSettings

from app.config import get_settings
from app.orchestrator import run_case
from app.schemas import SearchInput
from app.tasks_spatial import run_triangulation


async def run_case_task(ctx: dict[str, Any], case_id: str, input_json: dict, llm: str) -> None:
    await run_case(uuid.UUID(case_id), SearchInput(**input_json), llm=llm)


def _redis_settings() -> RedisSettings:
    s = get_settings()
    u = urlparse(s.redis_url)
    db = 0
    if u.path and u.path != "/":
        try:
            db = int(u.path.lstrip("/"))
        except ValueError:
            db = 0
    return RedisSettings(
        host=u.hostname or "redis",
        port=u.port or 6379,
        database=db,
        password=u.password or None,
    )


class WorkerSettings:
    functions = [run_case_task, run_triangulation]
    redis_settings = _redis_settings()
    max_jobs = 5
    job_timeout = 60 * 60
    keep_result = 60 * 60
