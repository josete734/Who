"""Photo clusterer (Wave 2 / B3).

Two strategies, picked at runtime:

1. **Face clustering** (preferred). Uses ``face_recognition`` (dlib) to
   compute a 128-d encoding per photo, then DBSCAN with eps=0.45 over the
   encodings.
2. **pHash fallback**. If ``face_recognition`` is not importable, falls
   back to grouping photos whose pHash Hamming distance is < 8.

Both paths persist results to ``photo_clusters`` and update
``photos.cluster_id``.
"""
from __future__ import annotations

import io
import logging
import struct
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image
from sqlalchemy import text

# face_recognition is optional - guard the import.
try:
    import face_recognition  # type: ignore
    _HAS_FACE = True
except Exception:  # pragma: no cover - dep guard
    face_recognition = None  # type: ignore
    _HAS_FACE = False

try:
    from sklearn.cluster import DBSCAN  # type: ignore
    _HAS_SKLEARN = True
except Exception:  # pragma: no cover
    DBSCAN = None  # type: ignore
    _HAS_SKLEARN = False

import httpx

from app.db import session_scope

log = logging.getLogger(__name__)

FACE_EPS = 0.45
PHASH_HAMMING_THRESHOLD = 8


# --- encoding (de)serialization --------------------------------------------
def encode_vec(v: np.ndarray) -> bytes:
    v = np.asarray(v, dtype=np.float32)
    return struct.pack("<I", v.size) + v.tobytes()


def decode_vec(b: bytes) -> np.ndarray:
    (n,) = struct.unpack("<I", b[:4])
    return np.frombuffer(b[4 : 4 + n * 4], dtype=np.float32)


# --- pHash helpers ---------------------------------------------------------
def _phash_to_int(s: str) -> int:
    return int(s, 16)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# --- public DBSCAN wrapper (used by tests) --------------------------------
def cluster_encodings(encodings: list[np.ndarray], eps: float = FACE_EPS) -> list[int]:
    """Cluster 128-d face encodings with DBSCAN. Returns labels (-1 = noise)."""
    if not encodings:
        return []
    if not _HAS_SKLEARN:
        raise RuntimeError("scikit-learn is required for face clustering")
    X = np.vstack(encodings)
    db = DBSCAN(eps=eps, min_samples=1, metric="euclidean").fit(X)
    return db.labels_.tolist()


def cluster_phashes(phashes: list[str], threshold: int = PHASH_HAMMING_THRESHOLD) -> list[int]:
    """Group pHashes by Hamming distance via union-find. Returns int labels per input."""
    n = len(phashes)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    ints = [_phash_to_int(p) for p in phashes]
    for i in range(n):
        for j in range(i + 1, n):
            if hamming(ints[i], ints[j]) < threshold:
                union(i, j)
    # remap roots to 0..k labels
    remap: dict[int, int] = {}
    out: list[int] = []
    for i in range(n):
        r = find(i)
        if r not in remap:
            remap[r] = len(remap)
        out.append(remap[r])
    return out


# --- face encoding extraction ---------------------------------------------
async def _load_image_bytes(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as c:
            r = await c.get(url)
            if r.status_code == 200:
                return r.content
    except Exception:
        return None
    return None


def encode_face_from_bytes(blob: bytes) -> np.ndarray | None:
    """Return the first 128-d face encoding found, or None."""
    if not _HAS_FACE:
        return None
    try:
        img = Image.open(io.BytesIO(blob)).convert("RGB")
        arr = np.array(img)
        encs = face_recognition.face_encodings(arr)
        if not encs:
            return None
        return np.asarray(encs[0], dtype=np.float32)
    except Exception:
        return None


@dataclass
class ClusterResult:
    method: str  # "face" | "phash"
    n_photos: int
    n_clusters: int


async def cluster_photos(case_id: uuid.UUID | str) -> dict[str, Any]:
    """Cluster all photos for `case_id`. Persists results in `photo_clusters`."""
    if isinstance(case_id, str):
        case_id = uuid.UUID(case_id)

    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, url, phash, face_encoding FROM photos "
                    "WHERE case_id = :cid"
                ),
                {"cid": str(case_id)},
            )
        ).all()

    if not rows:
        return ClusterResult("none", 0, 0).__dict__

    # Step 1: ensure encodings exist when face_recognition is available.
    photo_ids = [r.id for r in rows]
    encodings: dict[uuid.UUID, np.ndarray] = {}

    if _HAS_FACE:
        # Compute encodings for any photo that doesn't have one yet.
        missing = [(r.id, r.url) for r in rows if r.face_encoding is None]
        for pid, url in missing:
            blob = await _load_image_bytes(url)
            if not blob:
                continue
            enc = encode_face_from_bytes(blob)
            if enc is None:
                continue
            encodings[pid] = enc
            async with session_scope() as s:
                await s.execute(
                    text("UPDATE photos SET face_encoding = :e WHERE id = :id"),
                    {"e": encode_vec(enc), "id": str(pid)},
                )
        for r in rows:
            if r.face_encoding is not None and r.id not in encodings:
                try:
                    encodings[r.id] = decode_vec(bytes(r.face_encoding))
                except Exception:
                    continue

    # Step 2: choose method.
    if _HAS_FACE and encodings:
        ordered_ids = list(encodings.keys())
        labels = cluster_encodings([encodings[i] for i in ordered_ids])
        method = "face"
    else:
        # pHash fallback.
        ordered_ids = [r.id for r in rows if r.phash]
        phashes = [r.phash for r in rows if r.phash]
        labels = cluster_phashes(phashes) if phashes else []
        method = "phash"

    # Step 3: persist.
    cluster_uuid: dict[int, uuid.UUID] = {}
    async with session_scope() as s:
        # Reset prior clusters for this case (idempotent re-run).
        await s.execute(
            text("DELETE FROM photo_clusters WHERE case_id = :cid"),
            {"cid": str(case_id)},
        )
        await s.execute(
            text("UPDATE photos SET cluster_id = NULL WHERE case_id = :cid"),
            {"cid": str(case_id)},
        )

        # Group by label.
        groups: dict[int, list[uuid.UUID]] = {}
        for pid, lab in zip(ordered_ids, labels):
            if lab < 0:  # DBSCAN noise
                continue
            groups.setdefault(lab, []).append(pid)

        for lab, members in groups.items():
            cid = uuid.uuid4()
            cluster_uuid[lab] = cid
            await s.execute(
                text(
                    """
                    INSERT INTO photo_clusters
                      (id, case_id, label, count, representative_photo_id)
                    VALUES (:id, :cid, :lab, :n, :rep)
                    """
                ),
                {
                    "id": str(cid),
                    "cid": str(case_id),
                    "lab": f"{method}:{lab}",
                    "n": len(members),
                    "rep": str(members[0]),
                },
            )
            for pid in members:
                await s.execute(
                    text("UPDATE photos SET cluster_id = :cl WHERE id = :pid"),
                    {"cl": str(cid), "pid": str(pid)},
                )

    return ClusterResult(method, len(photo_ids), len(cluster_uuid)).__dict__
