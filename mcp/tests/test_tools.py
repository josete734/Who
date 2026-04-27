"""Tests for the who-mcp tool functions, with HTTP mocked via respx."""
from __future__ import annotations

import os

import httpx
import pytest
import respx

# Make sure WHO_BASE_URL is deterministic before importing the module.
os.environ["WHO_BASE_URL"] = "https://who.test"
os.environ["WHO_API_KEY"] = "test-token"

from mcp import server as srv  # noqa: E402

BASE = "https://who.test"


@pytest.fixture
def mock_router():
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.mark.asyncio
async def test_create_case_posts_payload_and_auth(mock_router):
    route = mock_router.post(f"{BASE}/api/cases").mock(
        return_value=httpx.Response(
            200, json={"case_id": "abc", "status": "queued", "llm": "gemini"}
        )
    )
    out = await srv.osint_create_case(
        inputs={"full_name": "Jane Doe", "email": "j@d.com"},
        legal_basis="art 6.1.f RGPD",
        legal_basis_note="prensa",
    )
    assert out["case_id"] == "abc"
    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer test-token"
    body = req.read().decode()
    assert "Jane Doe" in body
    assert "legal_basis_note" in body
    assert "art 6.1.f" in body


@pytest.mark.asyncio
async def test_create_case_title_fallback_full_name(mock_router):
    mock_router.post(f"{BASE}/api/cases").mock(
        return_value=httpx.Response(200, json={"case_id": "x", "status": "queued"})
    )
    await srv.osint_create_case(
        inputs={"full_name": "Alice"}, legal_basis="lb"
    )
    body = mock_router.calls.last.request.read().decode()
    assert "\"title\":\"Alice\"" in body or "\"title\": \"Alice\"" in body


@pytest.mark.asyncio
async def test_run_case_returns_status(mock_router):
    mock_router.get(f"{BASE}/api/cases/c1").mock(
        return_value=httpx.Response(200, json={"id": "c1", "status": "running"})
    )
    out = await srv.osint_run_case("c1")
    assert out["status"] == "running"


@pytest.mark.asyncio
async def test_get_findings_filters_kind_and_collector(mock_router):
    rows = [
        {"id": 1, "kind": "url", "collector": "searx"},
        {"id": 2, "kind": "email", "collector": "hibp"},
        {"id": 3, "kind": "url", "collector": "wayback"},
    ]
    mock_router.get(f"{BASE}/api/cases/c1/findings").mock(
        return_value=httpx.Response(200, json=rows)
    )
    out = await srv.osint_get_findings("c1", kind="url")
    assert {r["id"] for r in out} == {1, 3}

    mock_router.get(f"{BASE}/api/cases/c1/findings").mock(
        return_value=httpx.Response(200, json=rows)
    )
    out = await srv.osint_get_findings("c1", collector="hibp")
    assert [r["id"] for r in out] == [2]


@pytest.mark.asyncio
async def test_get_entities_passes_type_param(mock_router):
    route = mock_router.get(f"{BASE}/api/cases/c1/entities").mock(
        return_value=httpx.Response(200, json=[{"id": "e1", "type": "person"}])
    )
    out = await srv.osint_get_entities("c1", type="person")
    assert out[0]["type"] == "person"
    assert "type=person" in str(route.calls.last.request.url)


@pytest.mark.asyncio
async def test_investigate_posts_provider_and_steps(mock_router):
    route = mock_router.post(f"{BASE}/api/cases/c1/investigate").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    out = await srv.osint_investigate("c1", provider="claude", max_steps=4)
    assert out == {"ok": True}
    body = route.calls.last.request.read().decode()
    assert "claude" in body
    assert "4" in body


@pytest.mark.asyncio
async def test_export_passes_format_query(mock_router):
    route = mock_router.get(f"{BASE}/api/cases/c1/export").mock(
        return_value=httpx.Response(200, json={"format": "stix", "data": {}})
    )
    out = await srv.osint_export("c1", format="stix")
    assert out["format"] == "stix"
    assert "format=stix" in str(route.calls.last.request.url)


@pytest.mark.asyncio
async def test_request_raises_on_http_error(mock_router):
    mock_router.get(f"{BASE}/api/cases/missing").mock(
        return_value=httpx.Response(404, text="not found")
    )
    with pytest.raises(RuntimeError) as exc:
        await srv.osint_run_case("missing")
    assert "404" in str(exc.value)


def test_tools_registry_lists_all_six():
    names = {t.name for t in srv.TOOLS}
    assert names == {
        "osint_create_case",
        "osint_run_case",
        "osint_get_findings",
        "osint_get_entities",
        "osint_investigate",
        "osint_export",
    }


def test_dispatch_table_matches_registry():
    assert set(srv._DISPATCH) == {t.name for t in srv.TOOLS}
