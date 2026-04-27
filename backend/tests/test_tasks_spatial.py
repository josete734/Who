"""Tests for app.tasks_spatial.run_triangulation (Ola 1 / A1.4).

Drives the Arq task with an in-memory fake DB session and event bus to
verify it:
  * loads polyline-bearing findings for a case,
  * persists each inferred location into ``inferred_locations``,
  * emits a corresponding location ``Finding`` row,
  * publishes an ``inferred_location`` event for SSE.

Skips automatically when scikit-learn is not installed (matches the
behaviour of ``test_triangulation``).
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest

try:  # scikit-learn is only required for the triangulation tests.
    import sklearn  # type: ignore  # noqa: F401

    _HAVE_SKLEARN = True
except ImportError:
    _HAVE_SKLEARN = False

needs_sklearn = pytest.mark.skipif(not _HAVE_SKLEARN, reason="scikit-learn not installed")


# --------------------------------------------------------------------------- #
# Polyline encoding helper (mirrors test_triangulation)                        #
# --------------------------------------------------------------------------- #
def _encode_signed(value: int) -> str:
    value = ~(value << 1) if value < 0 else (value << 1)
    chunks: list[int] = []
    while value >= 0x20:
        chunks.append((0x20 | (value & 0x1F)) + 63)
        value >>= 5
    chunks.append(value + 63)
    return "".join(chr(c) for c in chunks)


def _encode_polyline(points: list[tuple[float, float]], precision: int = 5) -> str:
    factor = 10 ** precision
    out: list[str] = []
    plat = plon = 0
    for lat, lon in points:
        ilat = int(round(lat * factor))
        ilon = int(round(lon * factor))
        out.append(_encode_signed(ilat - plat))
        out.append(_encode_signed(ilon - plon))
        plat, plon = ilat, ilon
    return "".join(out)


HOME = (40.4168, -3.7038)  # Madrid


def _route_from_home(dx_km: float = 1.5) -> str:
    lat0, lon0 = HOME
    end = (lat0 + 0.009 * dx_km, lon0 + 0.012 * dx_km)
    return _encode_polyline([HOME, ((lat0 + end[0]) / 2, (lon0 + end[1]) / 2), end])


# --------------------------------------------------------------------------- #
# Fake DB session and event bus                                                #
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]):
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def scalar_one(self) -> Any:
        return self._rows[0][0] if self._rows else 0


class _FakeSession:
    def __init__(self, store: dict[str, list[Any]], findings_rows: list[tuple[uuid.UUID, dict]]):
        self._store = store
        self._findings_rows = findings_rows

    async def execute(self, stmt: Any, params: dict | None = None) -> _FakeResult:
        sql = str(stmt).lower()
        if "from findings" in sql and "select" in sql:
            return _FakeResult(self._findings_rows)
        if "insert into inferred_locations" in sql:
            self._store["inferred"].append(dict(params or {}))
            return _FakeResult([])
        return _FakeResult([])

    def add(self, obj: Any) -> None:
        self._store["findings_added"].append(obj)


@asynccontextmanager
async def _fake_session_scope_factory(store: dict[str, list[Any]],
                                       findings_rows: list[tuple[uuid.UUID, dict]]):
    yield _FakeSession(store, findings_rows)


# --------------------------------------------------------------------------- #
# Test                                                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@needs_sklearn
async def test_run_triangulation_emits_inferred_home(monkeypatch):
    from app import tasks_spatial as ts

    case_id = uuid.uuid4()
    base = datetime(2026, 4, 1, 6, 30)  # early morning => inferred_home
    findings_rows: list[tuple[uuid.UUID, dict]] = []
    for i in range(10):
        fid = uuid.uuid4()
        findings_rows.append(
            (
                fid,
                {
                    "polyline": _route_from_home(dx_km=1.5 + (i % 4) * 0.3),
                    "start_date_local": (base + timedelta(days=i, minutes=i * 7)).isoformat(),
                    "sport_type": "Run",
                },
            )
        )

    store: dict[str, list[Any]] = {
        "inferred": [],
        "findings_added": [],
        "events": [],
    }

    @asynccontextmanager
    async def _patched_session_scope():
        async with _fake_session_scope_factory(store, findings_rows) as s:
            yield s

    async def _fake_publish(case, event):
        store["events"].append((str(case), event))

    monkeypatch.setattr(ts, "session_scope", _patched_session_scope)
    monkeypatch.setattr(ts, "publish", _fake_publish)

    n = await ts.run_triangulation({}, str(case_id))

    assert n >= 1, "expected at least one inferred location to be emitted"

    # inferred_locations insert happened with sane payload
    assert store["inferred"], "no inferred_locations row inserted"
    row = store["inferred"][0]
    assert row["cid"] == str(case_id)
    assert row["kind"].startswith("inferred_")
    assert row["r"] >= 0
    assert 0.0 <= row["conf"] <= 1.0

    # location finding emitted
    assert store["findings_added"], "no Finding row added for inferred location"
    f = store["findings_added"][0]
    assert f.case_id == case_id
    assert f.entity_type == "location"
    assert f.collector == "triangulation"
    assert f.payload["kind"] == row["kind"]
    assert f.payload["source_activity_count"] >= 5
    assert "lat" in f.payload and "lon" in f.payload

    # SSE event published
    inferred_events = [e for _, e in store["events"] if e.get("type") == "inferred_location"]
    assert inferred_events, "expected an 'inferred_location' SSE event"
    ev = inferred_events[0]
    assert ev["case_id"] == str(case_id)
    for k in ("kind", "lat", "lon", "radius_m"):
        assert k in ev["data"]


@pytest.mark.asyncio
@needs_sklearn
async def test_run_triangulation_below_threshold_is_noop(monkeypatch):
    from app import tasks_spatial as ts

    case_id = uuid.uuid4()
    findings_rows: list[tuple[uuid.UUID, dict]] = [
        (uuid.uuid4(), {"polyline": _route_from_home(), "start_date_local": "2026-04-01T06:30:00"})
        for _ in range(3)
    ]
    store: dict[str, list[Any]] = {"inferred": [], "findings_added": [], "events": []}

    @asynccontextmanager
    async def _patched_session_scope():
        async with _fake_session_scope_factory(store, findings_rows) as s:
            yield s

    async def _fake_publish(case, event):
        store["events"].append((str(case), event))

    monkeypatch.setattr(ts, "session_scope", _patched_session_scope)
    monkeypatch.setattr(ts, "publish", _fake_publish)

    n = await ts.run_triangulation({}, str(case_id))
    assert n == 0
    assert store["inferred"] == []
    assert store["findings_added"] == []
    assert store["events"] == []
