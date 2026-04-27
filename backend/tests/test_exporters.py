"""Smoke tests for the case exporters (PDF / STIX 2.1 / MISP)."""
from __future__ import annotations

import datetime as dt
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Synthetic in-memory "db" + ORM-shaped Case/Finding objects
# ---------------------------------------------------------------------------


class _Case:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.title = "Test Case"
        self.legal_basis = "Art. 6(1)(f) GDPR — legitimate interest"
        self.created_at = dt.datetime(2026, 4, 27, 12, 0, tzinfo=dt.timezone.utc)
        self.finished_at = dt.datetime(2026, 4, 27, 12, 5, tzinfo=dt.timezone.utc)
        self.synthesis_markdown = "## Summary\n- example finding"


class _Finding:
    def __init__(self, **kw):
        self.id = uuid.uuid4()
        self.case_id = kw["case_id"]
        self.collector = kw.get("collector", "test")
        self.category = kw.get("category", "email")
        self.entity_type = kw.get("entity_type", "Profile")
        self.title = kw.get("title", "alice@example.com")
        self.url = kw.get("url", "https://example.com/alice")
        self.confidence = kw.get("confidence", 0.9)
        self.payload = kw.get("payload", {"source": "synthetic"})
        self.fingerprint = kw.get("fingerprint", "abc")
        self.created_at = kw.get("created_at", dt.datetime(2026, 4, 27, 12, 1, tzinfo=dt.timezone.utc))


class _ScalarsResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class _ExecResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarsResult(self._items)


class _FakeDB:
    """Mimics just enough of AsyncSession for the exporters."""

    def __init__(self, case: _Case, findings: list[_Finding]) -> None:
        self.case = case
        self.findings = findings

    async def get(self, model, pk):
        # pk may be uuid or str; normalize
        return self.case if str(pk) == str(self.case.id) else None

    async def execute(self, _stmt):
        return _ExecResult(self.findings)


@pytest.fixture
def fake_db():
    case = _Case()
    findings = [
        _Finding(case_id=case.id, category="email", title="alice@example.com"),
        _Finding(case_id=case.id, category="username", title="alice99",
                 url="https://twitter.com/alice99", entity_type="Profile"),
    ]
    return _FakeDB(case, findings), case, findings


@pytest.fixture(autouse=True)
def _stub_app_db_models(monkeypatch):
    """Make `from app.db import Case, Finding` resolve to our fakes
    so the exporters' isinstance/select calls don't depend on Postgres."""
    # The exporters only use Case/Finding for typing & .where(); a stub class
    # with a `case_id` attribute is enough for SQLAlchemy's `select(...).where`
    # — but since our _FakeDB.execute ignores the statement, we just need the
    # imports to succeed. The real app.db already defines them, so we don't
    # need to stub. This fixture is a no-op placeholder kept for clarity.
    yield


# ---------------------------------------------------------------------------
# STIX
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_stix_shape(fake_db):
    pytest.importorskip("stix2")
    pytest.importorskip("sqlalchemy")
    from app.exporters import export_stix

    db, case, findings = fake_db
    bundle = await export_stix(case.id, db)

    assert bundle["type"] == "bundle"
    assert "objects" in bundle and isinstance(bundle["objects"], list)
    types_present = {o["type"] for o in bundle["objects"]}
    assert "identity" in types_present
    assert "observed-data" in types_present
    assert "relationship" in types_present
    # one observed-data + one relationship per finding
    observed = [o for o in bundle["objects"] if o["type"] == "observed-data"]
    rels = [o for o in bundle["objects"] if o["type"] == "relationship"]
    assert len(observed) == len(findings)
    assert len(rels) == len(findings)
    # spec_version 2.1
    assert any(o.get("spec_version") == "2.1" for o in bundle["objects"]) or \
        bundle.get("spec_version") in (None, "2.1")


@pytest.mark.asyncio
async def test_export_stix_missing_case_raises(fake_db):
    pytest.importorskip("stix2")
    pytest.importorskip("sqlalchemy")
    from app.exporters import export_stix

    db, _case, _ = fake_db
    with pytest.raises(ValueError):
        await export_stix(uuid.uuid4(), db)


# ---------------------------------------------------------------------------
# MISP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_misp_shape(fake_db):
    pytest.importorskip("sqlalchemy")
    from app.exporters import export_misp

    db, case, findings = fake_db
    event = await export_misp(case.id, db)

    assert "Event" in event
    ev = event["Event"]
    assert ev["uuid"] == str(case.id)
    assert ev["info"].startswith("OSINT case:")
    assert isinstance(ev["Attribute"], list)
    assert len(ev["Attribute"]) == len(findings)
    # email finding mapped to email-src
    types = {a["type"] for a in ev["Attribute"]}
    assert "email-src" in types
    assert any(t["name"].startswith("tlp:") for t in ev["Tag"])


# ---------------------------------------------------------------------------
# PDF (weasyprint mocked – exercising template rendering only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_pdf_renders(monkeypatch, fake_db):
    pytest.importorskip("sqlalchemy")
    db, case, _ = fake_db

    fake_weasy = types.ModuleType("weasyprint")

    class _FakeHTML:
        def __init__(self, *a, **kw):
            self._kw = kw
            self._string = kw.get("string", "")
            assert "<html" in self._string.lower()
            assert str(case.id) in self._string
            assert "GDPR" in self._string or "RGPD" in self._string

        def write_pdf(self):
            return b"%PDF-1.4 fake\n%%EOF"

    fake_weasy.HTML = _FakeHTML  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "weasyprint", fake_weasy)

    from app.exporters import export_pdf

    data = await export_pdf(case.id, db)
    assert isinstance(data, (bytes, bytearray))
    assert data.startswith(b"%PDF")
