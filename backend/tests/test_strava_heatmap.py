"""Tests for the Strava heatmap collector (Wave 1 / A1.3)."""
from __future__ import annotations

import io

import httpx
import pytest
import respx

from app.schemas import SearchInput

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


# ---- helpers --------------------------------------------------------------


@pytest.fixture
def patch_get_client(monkeypatch):
    def _apply(module):
        async def _fake(_policy="default"):
            return httpx.AsyncClient(follow_redirects=True)

        monkeypatch.setattr(module, "get_client", _fake)
        return module

    return _apply


def _synthetic_tile_png() -> bytes:
    """Create a 256x256 RGBA PNG with a single dense 16x16 block."""
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    px = img.load()
    # Fully-opaque red block at block coord (4, 5).
    bx, by = 4, 5
    for dy in range(16):
        for dx in range(16):
            px[bx * 16 + dx, by * 16 + dy] = (255, 0, 0, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---- tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_cookie_returns_empty(monkeypatch, patch_get_client):
    from app.collectors import strava_heatmap as mod

    patch_get_client(mod)
    monkeypatch.delenv("STRAVA_HEATMAP_COOKIE", raising=False)

    async def _no_cookie() -> str | None:
        return None

    monkeypatch.setattr(mod, "_load_cookie", _no_cookie)

    findings = [
        f
        async for f in mod.StravaHeatmapCollector().run(
            SearchInput(extra_context="strava_bbox=40.40,-3.71,40.42,-3.69")
        )
    ]
    assert findings == []


@pytest.mark.asyncio
async def test_no_bbox_returns_empty(monkeypatch, patch_get_client):
    from app.collectors import strava_heatmap as mod

    patch_get_client(mod)
    monkeypatch.setenv("STRAVA_HEATMAP_COOKIE", "CloudFront-Policy=abc")

    findings = [
        f
        async for f in mod.StravaHeatmapCollector().run(
            SearchInput(extra_context="unrelated context")
        )
    ]
    assert findings == []


@pytest.mark.asyncio
async def test_emits_hotspots_from_synthetic_tile(monkeypatch, patch_get_client):
    from app.collectors import strava_heatmap as mod

    patch_get_client(mod)
    monkeypatch.setenv("STRAVA_HEATMAP_COOKIE", "CloudFront-Policy=abc; CloudFront-Signature=xyz")

    png = _synthetic_tile_png()

    with respx.mock(assert_all_called=False) as router:
        # Match any heatmap tile request across subdomains.
        router.get(
            url__regex=r"https://heatmap-external-[abc]\.strava\.com/tiles-auth/all/hot/13/\d+/\d+\.png.*"
        ).mock(return_value=httpx.Response(200, content=png, headers={"Content-Type": "image/png"}))

        # Tiny bbox: ~2 tiles maximum at zoom 13.
        si = SearchInput(extra_context="strava_bbox=40.4150,-3.7050,40.4170,-3.7030")
        findings = [f async for f in mod.StravaHeatmapCollector().run(si)]

    assert findings, "expected at least one hotspot finding"
    f = findings[0]
    assert f.collector == "strava_heatmap"
    assert f.category == "sport"
    assert f.entity_type == "hotspot"
    assert f.confidence == pytest.approx(0.6)
    p = f.payload
    assert p["source"] == "strava_heatmap"
    assert p["zoom"] == 13
    assert isinstance(p["tile_xyz"], list) and len(p["tile_xyz"]) == 3
    assert -90.0 <= p["lat"] <= 90.0
    assert -180.0 <= p["lon"] <= 180.0
    assert p["density"] >= 16


@pytest.mark.asyncio
async def test_inferred_home_fallback_builds_bbox(monkeypatch, patch_get_client):
    from app.collectors import strava_heatmap as mod

    patch_get_client(mod)
    monkeypatch.setenv("STRAVA_HEATMAP_COOKIE", "x=y")

    png = _synthetic_tile_png()
    with respx.mock(assert_all_called=False) as router:
        router.get(
            url__regex=r"https://heatmap-external-[abc]\.strava\.com/.*"
        ).mock(return_value=httpx.Response(200, content=png))

        si = SearchInput(
            extra_context="inferred_home_lat=40.4168, inferred_home_lon=-3.7038"
        )
        findings = [f async for f in mod.StravaHeatmapCollector().run(si)]

    assert findings


def test_tile_cap_constant():
    from app.collectors import strava_heatmap as mod

    assert mod.MAX_TILES_PER_CASE == 50
