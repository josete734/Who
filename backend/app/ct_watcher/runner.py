"""Arq-scheduled CT watcher tick.

Reads every row of ``ct_watchlist``, asks certspotter for issuances newer
than ``last_seen_id`` (per-domain high-water mark), persists deltas as
``Finding`` rows and publishes a ``ct.new_cert`` event on the case event
bus for each new certificate. Designed to run on a 5-minute cron via Arq.

The actual Arq registration is **not** done here — see WIRING block below.
"""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import text

from app.collectors.base import Finding as CollectorFinding
from app.db import Finding as DBFinding
from app.db import session_scope
from app.event_bus import publish
from app.http_util import client

logger = logging.getLogger(__name__)

CERTSPOTTER_URL = "https://api.certspotter.com/v1/issuances"
EVENT_KEY = "ct.new_cert"


async def _fetch_issuances(
    domain: str, after: str | None
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "domain": domain,
        "include_subdomains": "true",
        "expand": "dns_names,issuer,cert",
    }
    if after:
        params["after"] = after
    async with client(timeout=25) as c:
        try:
            r = await c.get(CERTSPOTTER_URL, params=params)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("ct_watcher fetch failed for %s: %s", domain, e)
            return []
    return data if isinstance(data, list) else []


def _row_to_finding_payload(row: dict[str, Any]) -> dict[str, Any]:
    issuer = ((row.get("issuer") or {}).get("name")) or row.get("issuer_name")
    return {
        "issuer": issuer,
        "valid_from": row.get("not_before"),
        "valid_to": row.get("not_after"),
        "certspotter_id": str(row.get("id") or ""),
        "dns_names": row.get("dns_names") or [],
    }


async def poll_domain(domain: str, case_id: uuid.UUID, last_seen_id: str | None) -> str | None:
    """Fetch new issuances for ``domain``, persist + emit events.

    Returns the new ``last_seen_id`` (max id observed) or the original value
    if no new rows were found.
    """
    rows = await _fetch_issuances(domain, last_seen_id)
    if not rows:
        return last_seen_id

    new_high_water = last_seen_id
    async with session_scope() as session:
        for row in rows:
            cs_id = str(row.get("id") or "")
            if not cs_id:
                continue
            payload = _row_to_finding_payload(row)
            primary_name = (payload["dns_names"][0] if payload["dns_names"] else domain).lstrip("*.").lower()

            cf = CollectorFinding(
                collector="ct_watcher",
                category="domain",
                entity_type="Subdomain",
                title=primary_name,
                url=f"https://api.certspotter.com/v1/issuances/{cs_id}",
                confidence=0.9,
                payload={"subdomain": primary_name, **payload},
            )
            db_row = DBFinding(
                case_id=case_id,
                collector=cf.collector,
                category=cf.category,
                entity_type=cf.entity_type,
                title=cf.title,
                url=cf.url,
                confidence=cf.confidence,
                payload=cf.payload,
                fingerprint=cf.fingerprint(),
            )
            session.add(db_row)

            await publish(
                case_id,
                {
                    "type": EVENT_KEY,
                    "data": {
                        "domain": domain,
                        "subdomain": primary_name,
                        "issuer": payload["issuer"],
                        "valid_from": payload["valid_from"],
                        "valid_to": payload["valid_to"],
                        "certspotter_id": cs_id,
                    },
                },
            )

            if new_high_water is None or cs_id > new_high_water:
                new_high_water = cs_id

    return new_high_water


async def run_watcher_tick(_ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single Arq tick: iterate the watchlist and poll each domain.

    Returns a small summary dict (handy for tests + Arq result inspection).
    """
    summary: dict[str, Any] = {"checked": 0, "updated": 0, "started_at": dt.datetime.utcnow().isoformat()}

    async with session_scope() as session:
        result = await session.execute(
            text("SELECT domain, case_id, last_seen_id FROM ct_watchlist")
        )
        rows = list(result.fetchall())

    for r in rows:
        domain = r[0]
        case_id = r[1] if isinstance(r[1], uuid.UUID) else uuid.UUID(str(r[1]))
        last_seen = r[2]
        summary["checked"] += 1
        try:
            new_high = await poll_domain(domain, case_id, last_seen)
        except Exception as e:  # noqa: BLE001
            logger.exception("ct_watcher poll_domain failed for %s: %s", domain, e)
            continue
        if new_high and new_high != last_seen:
            summary["updated"] += 1
            async with session_scope() as session:
                await session.execute(
                    text(
                        "UPDATE ct_watchlist SET last_seen_id = :lsid WHERE domain = :d"
                    ),
                    {"lsid": new_high, "d": domain},
                )

    return summary


# ---------------------------------------------------------------------------
# WIRING — TODO for the integration agent (Wave 3 / C9)
# ---------------------------------------------------------------------------
# To activate the watcher, add ``run_watcher_tick`` to Arq's WorkerSettings in
# ``backend/app/tasks.py``:
#
#     from arq import cron
#     from app.ct_watcher.runner import run_watcher_tick
#
#     class WorkerSettings:
#         functions = [run_case_task]
#         cron_jobs = [
#             cron(run_watcher_tick, minute={0, 5, 10, 15, 20, 25,
#                                            30, 35, 40, 45, 50, 55}),
#         ]
#
# The DB migration ``0003_ct_watchlist.sql`` must be applied before the first
# tick. Inserts into ``ct_watchlist(domain, case_id)`` are expected to come
# from a future API endpoint — also intentionally not wired here.
# ---------------------------------------------------------------------------
