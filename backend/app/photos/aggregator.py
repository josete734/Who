"""Photo aggregator (Wave 2 / B3).

Scans `findings.payload` for image URLs, downloads each blob with a strict
size cap and content-type whitelist, computes pHash + sha256, and persists
into the `photos` table created by migration 0002.

Public entry point::

    from app.photos.aggregator import collect_photos
    stats = await collect_photos(case_id)

The function is idempotent: the UNIQUE(case_id, sha256) constraint causes
re-runs to skip already-downloaded blobs.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx
from PIL import Image
from sqlalchemy import text

try:  # pHash is required; if missing the module still imports, errors at call.
    import imagehash  # type: ignore
except Exception:  # pragma: no cover - dep guard
    imagehash = None  # type: ignore

from app.db import session_scope
from app.photos.exif import parse_exif

log = logging.getLogger(__name__)

# --- limits ---------------------------------------------------------------
MAX_BYTES = 2 * 1024 * 1024  # 2 MB hard cap
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
PHOTO_FIELDS = ("avatar", "profile_pic", "profile_picture", "image", "photo", "picture")
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@dataclass
class CollectStats:
    scanned: int = 0
    downloaded: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "downloaded": self.downloaded,
            "skipped": self.skipped,
            "errors": self.errors[:20],
        }


def _extract_urls(payload: dict[str, Any]) -> Iterable[str]:
    """Yield image URLs from any of the configured fields, recursively."""
    if not isinstance(payload, dict):
        return
    for k, v in payload.items():
        if isinstance(v, str) and k.lower() in PHOTO_FIELDS and v.startswith(("http://", "https://")):
            yield v
        elif isinstance(v, dict):
            yield from _extract_urls(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    yield from _extract_urls(item)
                elif isinstance(item, str) and k.lower() in PHOTO_FIELDS and item.startswith(("http://", "https://")):
                    yield item


async def _download_one(client: httpx.AsyncClient, url: str) -> tuple[bytes, str] | None:
    """Stream-download with size cap + mime whitelist. Returns (bytes, mime) or None."""
    try:
        async with client.stream("GET", url, timeout=HTTP_TIMEOUT) as r:
            if r.status_code != 200:
                return None
            mime = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            if mime not in ALLOWED_MIME:
                return None
            clen = r.headers.get("content-length")
            if clen and int(clen) > MAX_BYTES:
                return None
            buf = bytearray()
            async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    return None
            return bytes(buf), mime
    except Exception as exc:  # pragma: no cover - network errors caught at runtime
        log.debug("photo download failed url=%s err=%s", url, exc)
        return None


def _hash_and_meta(blob: bytes) -> dict[str, Any] | None:
    """Compute sha256 + pHash + (w,h) for an image blob. Returns None on parse error."""
    sha = hashlib.sha256(blob).hexdigest()
    try:
        img = Image.open(io.BytesIO(blob))
        img.load()
    except Exception:
        return None
    w, h = img.size
    phash = None
    if imagehash is not None:
        try:
            phash = str(imagehash.phash(img))
        except Exception:
            phash = None
    return {"sha256": sha, "phash": phash, "width": w, "height": h}


async def collect_photos(case_id: uuid.UUID | str) -> dict[str, Any]:
    """Scan findings for `case_id`, download new photos, persist them.

    Returns a stats dict (see :class:`CollectStats`).
    """
    if isinstance(case_id, str):
        case_id = uuid.UUID(case_id)

    stats = CollectStats()

    async with session_scope() as s:
        rows = (
            await s.execute(
                text("SELECT id, payload FROM findings WHERE case_id = :cid"),
                {"cid": str(case_id)},
            )
        ).all()

    # Build (finding_id, url) candidates.
    candidates: list[tuple[uuid.UUID, str]] = []
    for r in rows:
        for url in _extract_urls(r.payload or {}):
            candidates.append((r.id, url))
    stats.scanned = len(candidates)
    if not candidates:
        return stats.as_dict()

    sem = asyncio.Semaphore(8)

    async with httpx.AsyncClient(follow_redirects=True) as client:

        async def _process(fid: uuid.UUID, url: str) -> dict[str, Any] | None:
            async with sem:
                got = await _download_one(client, url)
            if got is None:
                return None
            blob, mime = got
            meta = _hash_and_meta(blob)
            if meta is None:
                return None
            meta.update({"mime": mime, "url": url, "finding_id": fid})
            try:
                meta["exif"] = parse_exif(blob)
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("exif parse failed url=%s err=%s", url, exc)
                meta["exif"] = None
            # Keep raw bytes around for downstream vision analysis (A0.2).
            # Stripped before persistence — never sent to the DB.
            meta["_bytes"] = blob
            return meta

        results = await asyncio.gather(*(_process(f, u) for f, u in candidates))

    # WIRING: vision pipeline (A0.2). Built up inside the persistence loop
    # below by appending (photo_id, bytes, source_finding_id) for each
    # freshly inserted row. Consumed AFTER the loop in `_run_vision_pass`.
    from app.config import get_settings as _vision_get_settings
    _vision_settings = _vision_get_settings()
    _vision_enabled = bool(getattr(_vision_settings, "vision_enabled", False))
    _vision_budget = int(getattr(_vision_settings, "vision_max_per_case", 10) or 0)
    _vision_inserted: list[tuple[str, bytes, uuid.UUID | None]] = []

    async with session_scope() as s:
        for meta in results:
            if meta is None:
                stats.errors.append("download_or_parse_failed")
                continue
            exif = meta.get("exif") or {}
            gps_lat = exif.get("gps_lat") if exif else None
            gps_lon = exif.get("gps_lon") if exif else None
            taken_at = exif.get("taken_at") if exif else None
            try:
                res = await s.execute(
                    text(
                        """
                        INSERT INTO photos
                          (case_id, source_finding_id, url, sha256, phash,
                           width, height, mime,
                           gps_lat, gps_lon, taken_at,
                           camera_make, camera_model, lens_model, software, exif)
                        VALUES
                          (:cid, :fid, :url, :sha, :ph, :w, :h, :mime,
                           :glat, :glon, :taken_at,
                           :cmake, :cmodel, :lmodel, :sw,
                           CAST(:exif AS jsonb))
                        ON CONFLICT (case_id, sha256) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "cid": str(case_id),
                        "fid": str(meta["finding_id"]) if meta["finding_id"] else None,
                        "url": meta["url"],
                        "sha": meta["sha256"],
                        "ph": meta["phash"],
                        "w": meta["width"],
                        "h": meta["height"],
                        "mime": meta["mime"],
                        "glat": gps_lat,
                        "glon": gps_lon,
                        "taken_at": taken_at,
                        "cmake": exif.get("camera_make") if exif else None,
                        "cmodel": exif.get("camera_model") if exif else None,
                        "lmodel": exif.get("lens_model") if exif else None,
                        "sw": exif.get("software") if exif else None,
                        "exif": json.dumps(exif) if exif else None,
                    },
                )
                row = res.first()
                if row is not None:
                    stats.downloaded += 1
                    # WIRING: collect bytes for vision pass (A0.2).
                    if (
                        _vision_enabled
                        and len(_vision_inserted) < _vision_budget
                        and meta.get("_bytes")
                    ):
                        _vision_inserted.append(
                            (str(row[0]), meta["_bytes"], meta.get("finding_id"))
                        )
                    if gps_lat is not None and gps_lon is not None:
                        try:
                            await s.execute(
                                text(
                                    """
                                    INSERT INTO geo_signals
                                      (case_id, lat, lon, accuracy_km, kind,
                                       source_collector, evidence, confidence,
                                       finding_id, observed_at)
                                    VALUES
                                      (:cid, :lat, :lon, :acc, 'photo_gps',
                                       'photo_exif', CAST(:ev AS jsonb), 0.95,
                                       :fid, :obs)
                                    """
                                ),
                                {
                                    "cid": str(case_id),
                                    "lat": gps_lat,
                                    "lon": gps_lon,
                                    "acc": 0.05,
                                    "ev": json.dumps(
                                        {
                                            "photo_id": str(row[0]),
                                            "sha256": meta["sha256"],
                                            "taken_at": taken_at,
                                        }
                                    ),
                                    "fid": str(meta["finding_id"]) if meta["finding_id"] else None,
                                    "obs": taken_at,
                                },
                            )
                        except Exception as exc:  # pragma: no cover - defensive
                            stats.errors.append(f"geo_signal_insert: {exc}")
                else:
                    stats.skipped += 1
            except Exception as exc:  # pragma: no cover
                stats.errors.append(str(exc))

    # ------------------------------------------------------------------
    # Vision pass (A0.2): best-effort, never blocks the case.
    # ------------------------------------------------------------------
    if _vision_enabled and _vision_inserted:
        await _run_vision_pass(case_id, _vision_inserted, _vision_settings)

    return stats.as_dict()


async def _enqueue_vision(photo_id: str) -> bool:
    """Try to enqueue vision_analyze_photo on the Arq ``vision`` queue.

    Returns True on success. False signals "fall back to inline best-effort".
    """
    try:  # pragma: no cover - depends on runtime arq + redis availability
        from arq import create_pool as _create_pool

        from app.tasks import WorkerSettings
        pool = await _create_pool(WorkerSettings.redis_settings)
        await pool.enqueue_job(
            "vision_analyze_photo", photo_id, _queue_name="vision"
        )
        return True
    except Exception as exc:
        log.debug("vision enqueue unavailable, falling back inline: %s", exc)
        return False


async def _run_vision_pass(
    case_id: uuid.UUID,
    items: list[tuple[str, bytes, uuid.UUID | None]],
    settings: Any,
) -> None:
    """Run Ollama vision on each freshly inserted photo and persist results."""
    from app.photos.vision import analyze_photo

    for photo_id, blob, source_fid in items:
        if await _enqueue_vision(photo_id):
            continue
        try:
            vision = await asyncio.wait_for(
                analyze_photo(blob, settings=settings), timeout=30.0
            )
        except asyncio.TimeoutError:
            vision = {}
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("vision inline failed photo=%s: %s", photo_id, exc)
            vision = {}
        if not vision:
            continue
        await _persist_vision(case_id, photo_id, source_fid, vision)


async def _persist_vision(
    case_id: uuid.UUID,
    photo_id: str,
    source_fid: uuid.UUID | None,
    vision: dict[str, Any],
) -> None:
    """Update photos.vision and emit synthetic findings for the pivot extractor."""
    try:
        async with session_scope() as s:
            await s.execute(
                text("UPDATE photos SET vision = CAST(:v AS jsonb) WHERE id = :pid"),
                {"v": json.dumps(vision), "pid": photo_id},
            )

            inferred_city = (vision.get("inferred_city") or "").strip()
            if inferred_city:
                await s.execute(
                    text(
                        """
                        INSERT INTO findings
                          (case_id, collector, entity_type, title, payload, confidence)
                        VALUES
                          (:cid, :col, :et, :title, CAST(:pl AS jsonb), :c)
                        """
                    ),
                    {
                        "cid": str(case_id),
                        "col": "vision",
                        "et": "location",
                        "title": f"Vision inferred city: {inferred_city}",
                        "pl": json.dumps(
                            {
                                "photo_id": photo_id,
                                "inferred_city": inferred_city,
                                "inferred_country": vision.get("inferred_country") or "",
                                "source": "vision",
                            }
                        ),
                        "c": 0.5,
                    },
                )
            for v in vision.get("vehicles") or []:
                if not isinstance(v, dict):
                    continue
                plate = (v.get("plate") or "").strip()
                if not plate:
                    continue
                await s.execute(
                    text(
                        """
                        INSERT INTO findings
                          (case_id, collector, entity_type, title, payload, confidence)
                        VALUES
                          (:cid, :col, :et, :title, CAST(:pl AS jsonb), :c)
                        """
                    ),
                    {
                        "cid": str(case_id),
                        "col": "vision",
                        "et": "plate",
                        "title": f"Vision plate: {plate}",
                        "pl": json.dumps(
                            {
                                "photo_id": photo_id,
                                "plate": plate,
                                "make": v.get("make") or "",
                                "model": v.get("model") or "",
                                "source": "vision",
                            }
                        ),
                        "c": 0.5,
                    },
                )
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("vision persist failed photo=%s: %s", photo_id, exc)
