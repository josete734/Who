"""Tests for `app.photos.exif.parse_exif` (Wave 0 / A0.1).

The fixture JPEG is generated programmatically with Pillow + piexif-style
raw bytes via Pillow's own EXIF builder, so the tests are self-contained
and deterministic.
"""
from __future__ import annotations

import io
from pathlib import Path

from fractions import Fraction

from PIL import Image

from app.photos.exif import _gps_to_decimal, _parse_dt, parse_exif

# Known reference: Puerta del Sol, Madrid (≈40.4168, -3.7038).
REF_LAT = 40.4168
REF_LON = -3.7038
REF_DT = "2024:06:15 12:34:56"

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "photos"


def _to_dms_rationals(value: float) -> tuple[Fraction, Fraction, Fraction]:
    """Convert decimal degrees → (deg, min, sec) Fractions for Pillow EXIF."""
    value = abs(value)
    deg = int(value)
    rem = (value - deg) * 60
    minutes = int(rem)
    seconds = (rem - minutes) * 60
    return (
        Fraction(deg, 1),
        Fraction(minutes, 1),
        Fraction(int(round(seconds * 1000)), 1000),
    )


def _build_jpeg_with_gps() -> bytes:
    """Return a small JPEG with EXIF GPS + DateTimeOriginal embedded."""
    img = Image.new("RGB", (16, 16), color=(120, 50, 200))
    exif = img.getexif()

    # Top-level IFD0 fields
    exif[0x010F] = "TestMake"        # Make
    exif[0x0110] = "TestModel"       # Model
    exif[0x0131] = "pytest-fixture"  # Software
    exif[0x0132] = REF_DT            # DateTime

    # ExifIFD with DateTimeOriginal + LensModel
    try:
        from PIL.ExifTags import IFD

        exif_ifd = exif.get_ifd(IFD.Exif)
        exif_ifd[0x9003] = REF_DT           # DateTimeOriginal
        exif_ifd[0xA434] = "TestLens 50mm"  # LensModel

        gps_ifd = exif.get_ifd(IFD.GPSInfo)
        gps_ifd[1] = "N" if REF_LAT >= 0 else "S"
        gps_ifd[2] = _to_dms_rationals(REF_LAT)
        gps_ifd[3] = "E" if REF_LON >= 0 else "W"
        gps_ifd[4] = _to_dms_rationals(REF_LON)
    except Exception:
        # Older Pillow lacks IFD enum — fall back to setting GPSInfo via tag 0x8825.
        pass

    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _ensure_fixture_on_disk() -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / "with_gps.jpg"
    if not path.exists():
        path.write_bytes(_build_jpeg_with_gps())
    return path


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_gps_to_decimal_signs_correctly():
    rationals = ((40, 1), (25, 1), (4, 1))
    assert abs(_gps_to_decimal(rationals, "N") - 40.41777) < 1e-3
    assert abs(_gps_to_decimal(rationals, "S") + 40.41777) < 1e-3


def test_parse_dt_handles_exif_format():
    dt = _parse_dt("2024:06:15 12:34:56")
    assert dt is not None
    assert dt.year == 2024 and dt.month == 6 and dt.day == 15
    assert dt.hour == 12 and dt.minute == 34 and dt.second == 56
    assert _parse_dt(None) is None
    assert _parse_dt("") is None
    assert _parse_dt("garbage") is None


def test_parse_exif_with_gps_fixture():
    path = _ensure_fixture_on_disk()
    blob = path.read_bytes()
    out = parse_exif(blob)
    assert out is not None, "parse_exif returned None for a JPEG with EXIF"
    assert out["gps_lat"] is not None and out["gps_lon"] is not None
    assert abs(out["gps_lat"] - REF_LAT) < 1e-4
    assert abs(out["gps_lon"] - REF_LON) < 1e-4
    assert out["taken_at"] is not None
    assert out["taken_at"].startswith("2024-06-15T12:34:56")
    assert out["camera_make"] == "TestMake"
    assert out["camera_model"] == "TestModel"
    assert out["software"] == "pytest-fixture"
    assert isinstance(out["raw"], dict)


def test_parse_exif_without_exif_returns_none():
    img = Image.new("RGB", (8, 8), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    assert parse_exif(buf.getvalue()) is None


def test_parse_exif_empty_bytes_returns_none():
    assert parse_exif(b"") is None
    assert parse_exif(b"not-an-image") is None
