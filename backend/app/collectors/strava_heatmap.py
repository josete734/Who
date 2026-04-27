"""Strava global heatmap tile collector (Wave 1 / A1.3).

Downloads authenticated heatmap tiles for a bounding-box extracted from
``extra_context`` (or, as a fallback, from previously inferred locations
of the case stuffed into ``extra_context`` by the orchestrator). Each
tile is parsed pixel-by-pixel: blocks of 16x16 with the highest density
of non-transparent pixels become ``hotspot`` findings.

Authentication uses the CloudFront cookie set by Strava's web session;
either persisted in the ``strava_tokens`` table by A1.2 or supplied as
the ``STRAVA_HEATMAP_COOKIE`` environment variable. If neither is
available the collector is a silent no-op.

The collector is capped at ``MAX_TILES_PER_CASE`` requests to stay
gentle on Strava's CDN.
"""
from __future__ import annotations

import io
import logging
import math
import os
import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

TILE_URL = (
    "https://heatmap-external-{sub}.strava.com/tiles-auth/all/hot/{z}/{x}/{y}.png?v=19"
)
SUBDOMAINS = ("a", "b", "c")
ZOOM = 13
TILE_SIZE = 256
BLOCK_SIZE = 16  # → 16x16 = 256 blocks per tile
MAX_TILES_PER_CASE = 50
TOP_HOTSPOTS_PER_TILE = 3
MIN_DENSITY = 16  # non-transparent pixels (out of 256) to qualify
BBOX_RX = re.compile(
    r"strava_bbox\s*[=:]\s*"
    r"(-?\d+(?:\.\d+)?)\s*,\s*"
    r"(-?\d+(?:\.\d+)?)\s*,\s*"
    r"(-?\d+(?:\.\d+)?)\s*,\s*"
    r"(-?\d+(?:\.\d+)?)"
)
INFERRED_LATLON_RX = re.compile(
    r"inferred_home_lat\s*[=:]\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*"
    r"inferred_home_lon\s*[=:]\s*(-?\d+(?:\.\d+)?)"
)


def _parse_bbox(extra_context: str | None) -> tuple[float, float, float, float] | None:
    if not extra_context:
        return None
    m = BBOX_RX.search(extra_context)
    if m:
        lat_min, lon_min, lat_max, lon_max = (float(g) for g in m.groups())
        if lat_min > lat_max:
            lat_min, lat_max = lat_max, lat_min
        if lon_min > lon_max:
            lon_min, lon_max = lon_max, lon_min
        return (lat_min, lon_min, lat_max, lon_max)
    # Fallback: derive a ~5 km square around an inferred home point.
    m2 = INFERRED_LATLON_RX.search(extra_context)
    if m2:
        lat = float(m2.group(1))
        lon = float(m2.group(2))
        d_lat = 0.025  # ~2.7 km
        d_lon = 0.025 / max(math.cos(math.radians(lat)), 0.1)
        return (lat - d_lat, lon - d_lon, lat + d_lat, lon + d_lon)
    return None


def _deg2tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    lat_rad = math.radians(lat)
    n = 2.0**z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _tile2deg(x: float, y: float, z: int) -> tuple[float, float]:
    n = 2.0**z
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n)))
    return math.degrees(lat_rad), lon


def _bbox_tiles(
    bbox: tuple[float, float, float, float], z: int, cap: int
) -> list[tuple[int, int]]:
    lat_min, lon_min, lat_max, lon_max = bbox
    x_min, y_max = _deg2tile(lat_min, lon_min, z)
    x_max, y_min = _deg2tile(lat_max, lon_max, z)
    if x_min > x_max:
        x_min, x_max = x_max, x_min
    if y_min > y_max:
        y_min, y_max = y_max, y_min
    tiles: list[tuple[int, int]] = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            tiles.append((x, y))
            if len(tiles) >= cap:
                return tiles
    return tiles


async def _load_cookie() -> str | None:
    """CloudFront cookie from env or strava_tokens table (best-effort)."""
    env = os.getenv("STRAVA_HEATMAP_COOKIE")
    if env:
        return env.strip()
    try:
        from sqlalchemy import text

        from app.db import SessionLocal
    except Exception:  # pragma: no cover
        return None
    try:
        async with SessionLocal() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT cloudfront_cookie FROM strava_tokens "
                        "WHERE cloudfront_cookie IS NOT NULL "
                        "ORDER BY expires_at DESC NULLS LAST LIMIT 1"
                    )
                )
            ).first()
            if row and row[0]:
                return str(row[0])
    except Exception:
        # Table may not exist yet (A1.2 not landed) — silently fall through.
        return None
    return None


def _hotspots_from_png(
    png_bytes: bytes,
) -> list[tuple[int, int, int]]:
    """Return up to TOP_HOTSPOTS_PER_TILE (block_x, block_y, density) entries."""
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return []
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    except Exception:
        return []
    if img.size != (TILE_SIZE, TILE_SIZE):
        img = img.resize((TILE_SIZE, TILE_SIZE))
    px = img.load()
    blocks_per_side = TILE_SIZE // BLOCK_SIZE
    densities: list[tuple[int, int, int]] = []
    for by in range(blocks_per_side):
        for bx in range(blocks_per_side):
            count = 0
            for dy in range(BLOCK_SIZE):
                for dx in range(BLOCK_SIZE):
                    a = px[bx * BLOCK_SIZE + dx, by * BLOCK_SIZE + dy][3]
                    if a > 0:
                        count += 1
            if count >= MIN_DENSITY:
                densities.append((bx, by, count))
    densities.sort(key=lambda t: t[2], reverse=True)
    return densities[:TOP_HOTSPOTS_PER_TILE]


def _block_centroid_latlon(
    tile_x: int, tile_y: int, block_x: int, block_y: int, z: int
) -> tuple[float, float]:
    blocks_per_side = TILE_SIZE // BLOCK_SIZE
    frac_x = (block_x + 0.5) / blocks_per_side
    frac_y = (block_y + 0.5) / blocks_per_side
    return _tile2deg(tile_x + frac_x, tile_y + frac_y, z)


@register
class StravaHeatmapCollector(Collector):
    name = "strava_heatmap"
    category = "sport"
    needs = ("extra_context",)
    timeout_seconds = 60
    description = (
        "Strava global heatmap tile sampler — extracts hotspot centroids "
        "for a bbox from extra_context. Requires a CloudFront cookie."
    )

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        bbox = _parse_bbox(input.extra_context)
        if not bbox:
            return
        cookie = await _load_cookie()
        if not cookie:
            return

        tiles = _bbox_tiles(bbox, ZOOM, MAX_TILES_PER_CASE)
        if not tiles:
            return

        headers = {
            "Cookie": cookie,
            "Accept": "image/png,image/*;q=0.9,*/*;q=0.5",
            "Referer": "https://www.strava.com/heatmap",
        }
        client = await get_client("gentle")
        try:
            for idx, (tx, ty) in enumerate(tiles):
                sub = SUBDOMAINS[idx % len(SUBDOMAINS)]
                url = TILE_URL.format(sub=sub, z=ZOOM, x=tx, y=ty)
                try:
                    r = await client.get(url, headers=headers)
                except (httpx.HTTPError, OSError):
                    continue
                if r.status_code != 200 or not r.content:
                    continue
                hotspots = _hotspots_from_png(r.content)
                for bx, by, density in hotspots:
                    lat, lon = _block_centroid_latlon(tx, ty, bx, by, ZOOM)
                    yield Finding(
                        collector=self.name,
                        category="sport",
                        entity_type="hotspot",
                        title=f"Strava heatmap hotspot ({lat:.5f},{lon:.5f})",
                        url=None,
                        confidence=0.6,
                        payload={
                            "platform": "strava_heatmap",
                            "source": "strava_heatmap",
                            "lat": lat,
                            "lon": lon,
                            "density": density,
                            "zoom": ZOOM,
                            "tile_xyz": [tx, ty, ZOOM],
                            "block": [bx, by],
                        },
                    )
        finally:
            await client.aclose()


__all__ = [
    "StravaHeatmapCollector",
    "TILE_URL",
    "ZOOM",
    "MAX_TILES_PER_CASE",
]
