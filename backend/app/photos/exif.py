"""EXIF extraction for downloaded photo blobs (Wave 0 / A0.1).

Pure functions — no I/O, no DB. Given raw image bytes, returns a dict with
GPS coordinates (decimal degrees, signed by hemisphere), capture timestamp
(UTC ISO string), and the most relevant camera/software fields. The full
EXIF dict (JSON-serializable) is preserved under ``raw``.

Strategy:
1. Try Pillow's ``Image._getexif()`` (fast, no extra deps to read).
2. Fall back to ``exifread`` for files Pillow cannot decode (e.g. some
   makernote-heavy JPEGs).

Returns ``None`` if no usable EXIF metadata is present.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Any

from PIL import Image
from PIL.ExifTags import GPSTAGS, TAGS

try:  # optional fallback path
    import exifread  # type: ignore
except Exception:  # pragma: no cover - dep guard
    exifread = None  # type: ignore

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _to_float(x: Any) -> float | None:
    """Coerce a Pillow / exifread rational-ish value to float."""
    if x is None:
        return None
    try:
        # PIL IFDRational and Fraction expose float()
        return float(x)
    except (TypeError, ValueError):
        pass
    # exifread Ratio
    num = getattr(x, "num", None)
    den = getattr(x, "den", None)
    if num is not None and den:
        try:
            return float(num) / float(den)
        except (TypeError, ZeroDivisionError):
            return None
    # tuple/list of (num, den)
    if isinstance(x, (tuple, list)) and len(x) == 2:
        try:
            return float(x[0]) / float(x[1])
        except (TypeError, ZeroDivisionError):
            return None
    return None


def _gps_to_decimal(rationals: Any, ref: str | None) -> float | None:
    """Convert (deg, min, sec) rationals + N/S/E/W ref → signed decimal."""
    if rationals is None:
        return None
    try:
        seq = list(rationals)
    except TypeError:
        return None
    if len(seq) < 3:
        return None
    deg = _to_float(seq[0])
    minutes = _to_float(seq[1])
    seconds = _to_float(seq[2])
    if deg is None or minutes is None or seconds is None:
        return None
    val = deg + minutes / 60.0 + seconds / 3600.0
    if ref and str(ref).strip().upper() in ("S", "W"):
        val = -val
    return val


def _parse_dt(s: str | None) -> datetime | None:
    """Parse EXIF datetime strings (typically ``YYYY:MM:DD HH:MM:SS``) → UTC."""
    if not s:
        return None
    s = str(s).strip().rstrip("\x00")
    if not s:
        return None
    fmts = (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    )
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _serialize(v: Any) -> Any:
    """Make EXIF values JSON-serializable."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", errors="replace").rstrip("\x00")
        except Exception:
            return v.hex()
    if isinstance(v, (list, tuple)):
        return [_serialize(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _serialize(val) for k, val in v.items()}
    # IFDRational, Fraction, Ratio …
    f = _to_float(v)
    if f is not None:
        return f
    return str(v)


# ---------------------------------------------------------------------------
# Pillow path
# ---------------------------------------------------------------------------
def _parse_with_pillow(blob: bytes) -> dict[str, Any] | None:
    try:
        img = Image.open(io.BytesIO(blob))
        raw = img._getexif()  # type: ignore[attr-defined]
    except Exception:
        return None
    if not raw:
        return None

    tagged: dict[str, Any] = {}
    gps_raw: dict[str, Any] = {}
    for tag_id, value in raw.items():
        name = TAGS.get(tag_id, str(tag_id))
        if name == "GPSInfo" and isinstance(value, dict):
            for gtag_id, gval in value.items():
                gname = GPSTAGS.get(gtag_id, str(gtag_id))
                gps_raw[gname] = gval
            tagged[name] = {k: _serialize(v) for k, v in gps_raw.items()}
        else:
            tagged[name] = _serialize(value)

    lat = _gps_to_decimal(gps_raw.get("GPSLatitude"), gps_raw.get("GPSLatitudeRef"))
    lon = _gps_to_decimal(gps_raw.get("GPSLongitude"), gps_raw.get("GPSLongitudeRef"))

    dt = _parse_dt(tagged.get("DateTimeOriginal") or tagged.get("DateTime") or tagged.get("DateTimeDigitized"))

    return {
        "gps_lat": lat,
        "gps_lon": lon,
        "taken_at": dt.isoformat() if dt else None,
        "camera_make": tagged.get("Make"),
        "camera_model": tagged.get("Model"),
        "lens_model": tagged.get("LensModel"),
        "software": tagged.get("Software"),
        "raw": tagged,
    }


# ---------------------------------------------------------------------------
# exifread fallback
# ---------------------------------------------------------------------------
def _parse_with_exifread(blob: bytes) -> dict[str, Any] | None:
    if exifread is None:
        return None
    try:
        tags = exifread.process_file(io.BytesIO(blob), details=False)
    except Exception:
        return None
    if not tags:
        return None

    def _g(name: str) -> Any:
        v = tags.get(name)
        return v.values if v is not None else None

    lat_vals = _g("GPS GPSLatitude")
    lon_vals = _g("GPS GPSLongitude")
    lat_ref = _g("GPS GPSLatitudeRef")
    lon_ref = _g("GPS GPSLongitudeRef")
    if isinstance(lat_ref, list) and lat_ref:
        lat_ref = lat_ref[0]
    if isinstance(lon_ref, list) and lon_ref:
        lon_ref = lon_ref[0]

    lat = _gps_to_decimal(lat_vals, lat_ref if isinstance(lat_ref, str) else None)
    lon = _gps_to_decimal(lon_vals, lon_ref if isinstance(lon_ref, str) else None)

    dt_raw = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime") or tags.get("EXIF DateTimeDigitized")
    dt = _parse_dt(str(dt_raw) if dt_raw else None)

    raw = {k: _serialize(v.values if hasattr(v, "values") else v) for k, v in tags.items()}

    return {
        "gps_lat": lat,
        "gps_lon": lon,
        "taken_at": dt.isoformat() if dt else None,
        "camera_make": str(tags["Image Make"]) if "Image Make" in tags else None,
        "camera_model": str(tags["Image Model"]) if "Image Model" in tags else None,
        "lens_model": str(tags["EXIF LensModel"]) if "EXIF LensModel" in tags else None,
        "software": str(tags["Image Software"]) if "Image Software" in tags else None,
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# public entrypoint
# ---------------------------------------------------------------------------
def parse_exif(image_bytes: bytes) -> dict[str, Any] | None:
    """Extract EXIF metadata from a JPEG/TIFF blob.

    Returns ``None`` when no EXIF is present or the image cannot be parsed.
    """
    if not image_bytes:
        return None
    res = _parse_with_pillow(image_bytes)
    if res is not None:
        return res
    return _parse_with_exifread(image_bytes)
