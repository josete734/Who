"""GeoSignal extraction from heterogeneous evidence.

Sources:
  * IPs from the ``ipinfo`` collector payload (already carries lat,lon).
  * Address-like fields from registry collectors (BORME/BOE/companies),
    geocoded via an offline Nominatim cache + polite live fallback.
  * Place names from social posts/profiles (location field, post tags).
  * Timezone hints from github commit metadata (rough centroid only).

The geocoder uses a *DB-backed* cache (table ``geo_cache``) so we never
hammer the public Nominatim service. Live calls are rate-limited to
1 req/sec with a polite UA, per the OSM usage policy.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

try:  # optional, package not present on every dev box
    from geopy.geocoders import Nominatim  # type: ignore
    from geopy.extra.rate_limiter import RateLimiter  # type: ignore
    _GEOPY_OK = True
except Exception:  # pragma: no cover - graceful degrade
    Nominatim = None  # type: ignore
    RateLimiter = None  # type: ignore
    _GEOPY_OK = False

try:  # pycountry is optional at runtime
    import pycountry  # type: ignore
except Exception:  # pragma: no cover
    pycountry = None  # type: ignore


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
SignalKind = str  # "ip" | "address" | "social_place" | "tz_hint" | "exif"


@dataclass(slots=True)
class GeoSignal:
    lat: float
    lon: float
    accuracy_km: float
    kind: SignalKind
    source_collector: str
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    finding_id: str | None = None
    observed_at: dt.datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.observed_at is not None:
            d["observed_at"] = self.observed_at.isoformat()
        return d


# ---------------------------------------------------------------------------
# Static lookup tables (small, embedded — we never download at runtime)
# ---------------------------------------------------------------------------
# Approximate centroids per IANA timezone. Used for `tz_hint` signals
# coming from github commits etc. Accuracy is intentionally coarse.
_TZ_CENTROIDS: dict[str, tuple[float, float, float]] = {
    # tz: (lat, lon, accuracy_km)
    "Europe/Madrid": (40.4168, -3.7038, 400.0),
    "Europe/London": (51.5074, -0.1278, 250.0),
    "Europe/Paris": (48.8566, 2.3522, 350.0),
    "Europe/Berlin": (52.5200, 13.4050, 300.0),
    "Europe/Lisbon": (38.7223, -9.1393, 200.0),
    "America/New_York": (40.7128, -74.0060, 600.0),
    "America/Los_Angeles": (34.0522, -118.2437, 500.0),
    "America/Mexico_City": (19.4326, -99.1332, 500.0),
    "America/Argentina/Buenos_Aires": (-34.6037, -58.3816, 500.0),
    "Asia/Tokyo": (35.6762, 139.6503, 400.0),
    "Asia/Shanghai": (31.2304, 121.4737, 800.0),
    "UTC": (0.0, 0.0, 5000.0),
}

# Tiny offline gazetteer used as primary cache when DB not initialised.
_OFFLINE_GAZETTEER: dict[str, tuple[float, float, float]] = {
    "madrid": (40.4168, -3.7038, 10.0),
    "barcelona": (41.3851, 2.1734, 10.0),
    "valencia": (39.4699, -0.3763, 10.0),
    "sevilla": (37.3891, -5.9845, 10.0),
    "bilbao": (43.2630, -2.9350, 10.0),
    "lisboa": (38.7223, -9.1393, 10.0),
    "lisbon": (38.7223, -9.1393, 10.0),
    "porto": (41.1579, -8.6291, 10.0),
    "london": (51.5074, -0.1278, 10.0),
    "paris": (48.8566, 2.3522, 10.0),
    "berlin": (52.5200, 13.4050, 10.0),
    "new york": (40.7128, -74.0060, 10.0),
    "san francisco": (37.7749, -122.4194, 10.0),
    "buenos aires": (-34.6037, -58.3816, 10.0),
    "ciudad de mexico": (19.4326, -99.1332, 10.0),
    "mexico city": (19.4326, -99.1332, 10.0),
}


# ---------------------------------------------------------------------------
# Geocoder with DB cache + polite live fallback
# ---------------------------------------------------------------------------
class _Geocoder:
    """Singleton-ish wrapper around the offline cache + geopy Nominatim.

    All live calls are serialised through a 1 req/sec rate limiter and
    stamped with a polite UA string identifying this tool.
    """

    _UA = "osint-tool/1.0 (geo-extractor; contact: ops@osint-tool.local)"
    _instance: "_Geocoder | None" = None

    def __init__(self) -> None:
        self._mem: dict[str, tuple[float, float, float]] = dict(_OFFLINE_GAZETTEER)
        self._last_call_ts: float = 0.0
        self._lock = asyncio.Lock()
        self._geocode = None
        if _GEOPY_OK:
            try:
                geolocator = Nominatim(user_agent=self._UA, timeout=5)  # type: ignore[arg-type]
                self._geocode = RateLimiter(  # type: ignore[misc]
                    geolocator.geocode, min_delay_seconds=1.0
                )
            except Exception:
                self._geocode = None

    @classmethod
    def get(cls) -> "_Geocoder":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _norm(name: str) -> str:
        return re.sub(r"\s+", " ", name.strip().lower())

    @staticmethod
    def _hash(name: str) -> str:
        return hashlib.sha1(name.encode("utf-8")).hexdigest()[:24]

    async def _db_lookup(self, key: str) -> tuple[float, float, float] | None:
        try:
            from sqlalchemy import text  # local import, optional in tests
            from app.db import session_scope
        except Exception:
            return None
        try:
            async with session_scope() as s:
                row = (
                    await s.execute(
                        text("SELECT lat, lon, accuracy_km FROM geo_cache WHERE key = :k"),
                        {"k": key},
                    )
                ).first()
                if row is None:
                    return None
                return float(row[0]), float(row[1]), float(row[2])
        except Exception:
            return None

    async def _db_store(self, key: str, name: str, lat: float, lon: float, acc: float) -> None:
        try:
            from sqlalchemy import text
            from app.db import session_scope
        except Exception:
            return
        try:
            async with session_scope() as s:
                await s.execute(
                    text(
                        """
                        INSERT INTO geo_cache (key, name, lat, lon, accuracy_km)
                        VALUES (:k, :n, :lat, :lon, :acc)
                        ON CONFLICT (key) DO NOTHING
                        """
                    ),
                    {"k": key, "n": name, "lat": lat, "lon": lon, "acc": acc},
                )
        except Exception:
            return

    async def geocode(
        self, name: str, *, allow_live: bool = True
    ) -> tuple[float, float, float] | None:
        """Return (lat, lon, accuracy_km) or None."""
        if not name or not name.strip():
            return None
        norm = self._norm(name)
        if norm in self._mem:
            return self._mem[norm]
        key = self._hash(norm)
        cached = await self._db_lookup(key)
        if cached is not None:
            self._mem[norm] = cached
            return cached
        if not allow_live or self._geocode is None:
            return None
        async with self._lock:
            # Rate-limit guard (geopy's RateLimiter is sync; we ensure we
            # never issue more than 1/sec across coroutines too).
            wait = 1.0 - (time.monotonic() - self._last_call_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                loc = await asyncio.to_thread(self._geocode, name)
            except Exception:
                loc = None
            self._last_call_ts = time.monotonic()
        if loc is None:
            return None
        triple = (float(loc.latitude), float(loc.longitude), 5.0)
        self._mem[norm] = triple
        await self._db_store(key, norm, *triple)
        return triple


# ---------------------------------------------------------------------------
# Per-source extractors
# ---------------------------------------------------------------------------
_ADDRESS_FIELDS = ("address", "domicilio", "direccion", "location", "place", "city", "ciudad")


def _signal_from_ip_payload(p: dict[str, Any], collector: str, fid: str | None) -> GeoSignal | None:
    loc = p.get("loc")
    lat, lon = None, None
    if isinstance(loc, str) and "," in loc:
        try:
            a, b = loc.split(",", 1)
            lat, lon = float(a), float(b)
        except ValueError:
            pass
    if lat is None and "latitude" in p and "longitude" in p:
        try:
            lat, lon = float(p["latitude"]), float(p["longitude"])
        except (TypeError, ValueError):
            return None
    if lat is None or lon is None:
        return None
    city = p.get("city") or "?"
    country = p.get("country") or "?"
    return GeoSignal(
        lat=lat,
        lon=lon,
        accuracy_km=25.0,  # IP-geo is city-level at best
        kind="ip",
        source_collector=collector,
        evidence={"ip": p.get("ip"), "city": city, "country": country, "org": p.get("org")},
        confidence=0.65,
        finding_id=fid,
    )


async def _signal_from_address_payload(
    p: dict[str, Any], collector: str, fid: str | None, geocoder: _Geocoder, *, allow_live: bool
) -> GeoSignal | None:
    name = None
    for k in _ADDRESS_FIELDS:
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break
    if name is None:
        return None
    g = await geocoder.geocode(name, allow_live=allow_live)
    if g is None:
        return None
    lat, lon, acc = g
    return GeoSignal(
        lat=lat,
        lon=lon,
        accuracy_km=acc,
        kind="address",
        source_collector=collector,
        evidence={"query": name},
        confidence=0.75,
        finding_id=fid,
    )


async def _signal_from_social_payload(
    p: dict[str, Any], collector: str, fid: str | None, geocoder: _Geocoder, *, allow_live: bool
) -> GeoSignal | None:
    place = p.get("place") or p.get("location") or p.get("hometown")
    if not isinstance(place, str) or not place.strip():
        return None
    g = await geocoder.geocode(place, allow_live=allow_live)
    if g is None:
        return None
    lat, lon, acc = g
    return GeoSignal(
        lat=lat,
        lon=lon,
        accuracy_km=acc,
        kind="social_place",
        source_collector=collector,
        evidence={"place": place},
        confidence=0.55,
        finding_id=fid,
    )


def _signal_from_tz(p: dict[str, Any], collector: str, fid: str | None) -> GeoSignal | None:
    tz = p.get("timezone") or p.get("tz")
    if not isinstance(tz, str):
        return None
    centroid = _TZ_CENTROIDS.get(tz)
    if centroid is None:
        return None
    lat, lon, acc = centroid
    return GeoSignal(
        lat=lat,
        lon=lon,
        accuracy_km=acc,
        kind="tz_hint",
        source_collector=collector,
        evidence={"tz": tz},
        confidence=0.25,
        finding_id=fid,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def extract_signals(
    findings: Iterable[dict[str, Any]],
    *,
    allow_live_geocoding: bool = True,
) -> list[GeoSignal]:
    """Extract GeoSignals from an iterable of finding dicts.

    Each finding is expected to have ``collector``, ``payload``, optional
    ``id``. The function dispatches by collector name + payload heuristics.
    """
    geocoder = _Geocoder.get()
    out: list[GeoSignal] = []
    for f in findings:
        collector = str(f.get("collector") or "unknown")
        payload = f.get("payload") or {}
        if not isinstance(payload, dict):
            try:
                payload = json.loads(payload)  # type: ignore[arg-type]
            except Exception:
                payload = {}
        fid = str(f["id"]) if f.get("id") is not None else None

        if collector == "ipinfo" or "ip" in payload and ("loc" in payload or "latitude" in payload):
            s = _signal_from_ip_payload(payload, collector, fid)
            if s is not None:
                out.append(s)
                continue

        if collector in {"github", "gitlab"} or "timezone" in payload or "tz" in payload:
            s = _signal_from_tz(payload, collector, fid)
            if s is not None:
                out.append(s)
                # Don't continue — same finding could carry a place too.

        if any(k in payload for k in _ADDRESS_FIELDS):
            s = await _signal_from_address_payload(
                payload, collector, fid, geocoder, allow_live=allow_live_geocoding
            )
            if s is not None:
                out.append(s)
                continue

        if "place" in payload or "hometown" in payload:
            s = await _signal_from_social_payload(
                payload, collector, fid, geocoder, allow_live=allow_live_geocoding
            )
            if s is not None:
                out.append(s)

    return out


async def persist_signals(case_id: str, signals: Iterable[GeoSignal]) -> int:
    """Insert extracted signals into ``geo_signals``. Returns count."""
    from sqlalchemy import text
    from app.db import session_scope

    sigs = list(signals)
    if not sigs:
        return 0
    async with session_scope() as s:
        for sig in sigs:
            await s.execute(
                text(
                    """
                    INSERT INTO geo_signals
                        (case_id, lat, lon, accuracy_km, kind,
                         source_collector, evidence, confidence, finding_id)
                    VALUES
                        (:cid, :lat, :lon, :acc, :kind,
                         :sc, CAST(:ev AS JSONB), :conf, :fid)
                    """
                ),
                {
                    "cid": case_id,
                    "lat": sig.lat,
                    "lon": sig.lon,
                    "acc": sig.accuracy_km,
                    "kind": sig.kind,
                    "sc": sig.source_collector,
                    "ev": json.dumps(sig.evidence),
                    "conf": sig.confidence,
                    "fid": sig.finding_id,
                },
            )
    return len(sigs)
