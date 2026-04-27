"""Tests for the photos subsystem (Wave 2 / B3).

Covers:
  * pHash-based clustering correctness on near-duplicate hashes.
  * DBSCAN clustering over synthetic 128-d face encodings.
  * Aggregator HTTP behaviour with `respx`-mocked endpoints
    (size cap, mime whitelist) - the DB layer is monkey-patched out.
"""
from __future__ import annotations

import io
import struct
import uuid
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
import pytest
from PIL import Image

from app.photos import aggregator as agg_mod
from app.photos.clusterer import (
    cluster_encodings,
    cluster_phashes,
    decode_vec,
    encode_vec,
    hamming,
)


# --------------------------------------------------------------------------
# pHash clustering
# --------------------------------------------------------------------------
def test_hamming_basic() -> None:
    assert hamming(0, 0) == 0
    assert hamming(0xFF, 0x00) == 8
    assert hamming(0xF0F0, 0x0F0F) == 16


def test_cluster_phashes_groups_near_duplicates() -> None:
    # Two close pairs + one outlier (16 hex chars = 64-bit pHash).
    a1 = "ffffffffffffffff"
    a2 = "fffffffffffffffe"        # hamming 1 from a1 -> same group
    b1 = "0000000000000000"
    b2 = "0000000000000003"        # hamming 2 from b1 -> same group
    c1 = "00ff00ff00ff00ff"        # well separated -> own group

    labels = cluster_phashes([a1, a2, b1, b2, c1], threshold=8)
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]
    assert labels[4] != labels[0]
    assert labels[4] != labels[2]


# --------------------------------------------------------------------------
# DBSCAN over synthetic encodings
# --------------------------------------------------------------------------
def test_cluster_encodings_dbscan_synthetic() -> None:
    pytest.importorskip("sklearn")
    rng = np.random.default_rng(42)
    # Two tight clusters in 128-d, far apart.
    centroid_a = rng.normal(0.0, 0.01, size=128).astype(np.float32)
    centroid_b = centroid_a + 5.0  # very far -> distinct cluster
    encs = []
    for _ in range(5):
        encs.append(centroid_a + rng.normal(0.0, 0.01, 128).astype(np.float32))
    for _ in range(5):
        encs.append(centroid_b + rng.normal(0.0, 0.01, 128).astype(np.float32))

    labels = cluster_encodings(encs, eps=0.45)
    assert len(labels) == 10
    assert len(set(labels[:5])) == 1
    assert len(set(labels[5:])) == 1
    assert labels[0] != labels[5]


def test_encode_decode_vec_roundtrip() -> None:
    v = np.arange(128, dtype=np.float32) / 10.0
    out = decode_vec(encode_vec(v))
    assert np.allclose(out, v)


# --------------------------------------------------------------------------
# Aggregator URL extraction
# --------------------------------------------------------------------------
def test_extract_urls_handles_nested_payload() -> None:
    payload = {
        "avatar": "https://example.com/a.jpg",
        "nested": {"profile_pic": "https://example.com/b.png"},
        "list_field": [
            {"image": "https://example.com/c.webp"},
            {"unrelated": "no"},
        ],
        "ignored": "https://example.com/x.jpg",  # field name not whitelisted
    }
    urls = sorted(agg_mod._extract_urls(payload))
    assert urls == [
        "https://example.com/a.jpg",
        "https://example.com/b.png",
        "https://example.com/c.webp",
    ]


# --------------------------------------------------------------------------
# Aggregator download path - respx + DB stub
# --------------------------------------------------------------------------
def _png_bytes(size: tuple[int, int] = (16, 16), color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeResult:
    def __init__(self, rows: list[Any] = (), one: Any = None) -> None:
        self._rows = list(rows)
        self._one = one

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._one


class _Row:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _FakeSession:
    """Minimal AsyncSession stub: just enough for aggregator.collect_photos."""

    def __init__(self, findings_rows: list[_Row]) -> None:
        self.findings_rows = findings_rows
        self.inserts: list[dict] = []

    async def execute(self, stmt, params: dict | None = None):  # type: ignore[no-untyped-def]
        sql = str(stmt).lower()
        if "from findings" in sql:
            return _FakeResult(rows=self.findings_rows)
        if "insert into photos" in sql:
            self.inserts.append(params or {})
            return _FakeResult(one=_Row(id=uuid.uuid4()))
        return _FakeResult()


@pytest.fixture
def patch_session(monkeypatch: pytest.MonkeyPatch):
    """Replace `session_scope` in the aggregator module with a fake."""
    holder: dict[str, _FakeSession] = {}

    def make(findings_rows: list[_Row]) -> _FakeSession:
        sess = _FakeSession(findings_rows)
        holder["sess"] = sess

        @asynccontextmanager
        async def fake_scope():
            yield sess

        monkeypatch.setattr(agg_mod, "session_scope", fake_scope)
        return sess

    return make


async def test_collect_photos_downloads_and_filters(patch_session) -> None:
    respx = pytest.importorskip("respx")
    import httpx as _httpx

    case_id = uuid.uuid4()
    finding_id = uuid.uuid4()

    payload = {
        "avatar": "https://img.test/ok.png",
        "image": "https://img.test/bad-mime.txt",
        "profile_pic": "https://img.test/too-big.png",
    }
    sess = patch_session([_Row(id=finding_id, payload=payload)])

    ok_blob = _png_bytes()
    big_blob = b"\x00" * (agg_mod.MAX_BYTES + 100)

    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://img.test/ok.png").mock(
            return_value=_httpx.Response(200, content=ok_blob, headers={"content-type": "image/png"})
        )
        mock.get("https://img.test/bad-mime.txt").mock(
            return_value=_httpx.Response(200, content=b"hello", headers={"content-type": "text/plain"})
        )
        mock.get("https://img.test/too-big.png").mock(
            return_value=_httpx.Response(
                200,
                content=big_blob,
                headers={
                    "content-type": "image/png",
                    "content-length": str(len(big_blob)),
                },
            )
        )

        stats = await agg_mod.collect_photos(case_id)

    assert stats["scanned"] == 3
    # Only the OK one should have been inserted.
    assert len(sess.inserts) == 1
    inserted = sess.inserts[0]
    assert inserted["mime"] == "image/png"
    assert inserted["url"] == "https://img.test/ok.png"
    assert inserted["sha"] and len(inserted["sha"]) == 64
    assert inserted["w"] == 16 and inserted["h"] == 16
    assert stats["downloaded"] == 1
