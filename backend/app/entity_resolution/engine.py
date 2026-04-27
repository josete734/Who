"""Entity resolution engine.

Reads all Findings for a case from the DB, normalizes raw values into
candidate Entities, applies pairwise match rules, and clusters via union-find.
The output is a list of resolved Entities with aggregated confidence scores
ready to persist to the `entities` / `entity_sources` tables.

# WIRING:
# After all collectors finish (in app/orchestrator.py just before marking the
# case as 'done'), the orchestrator should add this single line:
#     await entity_resolution.engine.resolve(case_id)
#
# (import: `from app import entity_resolution`)
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from typing import TYPE_CHECKING

from app.entity_resolution.entities import Entity, EntitySource

if TYPE_CHECKING:  # pragma: no cover
    from app.db import Finding as DbFinding
from app.entity_resolution.match import best_match
from app.entity_resolution.normalize import (
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_url,
    normalize_username,
)
from app.entity_resolution.scoring import combine_confidences

# ---------------------------------------------------------------------------
# Finding → candidate entities
# ---------------------------------------------------------------------------

# Map (category, entity_type) → set of Entity types this finding can produce.
# Findings often imply multiple entities; we extract every signal we can.

def _candidates_from_finding(f: DbFinding) -> list[Entity]:
    out: list[Entity] = []
    payload: dict[str, Any] = f.payload or {}
    src = EntitySource(
        collector=f.collector,
        confidence=float(f.confidence or 0.0),
        raw_finding_id=f.id,
        observed_at=f.created_at,
    )

    def emit(t: str, value: str | None, attrs: dict[str, Any] | None = None) -> None:
        if not value:
            return
        e = Entity(case_id=f.case_id, type=t, value=value, attrs=attrs or {})
        e.add_source(src)
        out.append(e)

    # ---- Email signals ------------------------------------------------
    for key in ("email", "author_email"):
        em = normalize_email(payload.get(key))
        if em:
            emit("Email", em, {"author_login": payload.get("author_login")})

    # ---- Phone --------------------------------------------------------
    for key in ("phone", "number", "msisdn"):
        ph = normalize_phone(payload.get(key))
        if ph:
            emit("Phone", ph)

    # ---- Domain / URL -------------------------------------------------
    if f.url:
        nu = normalize_url(f.url)
        if nu:
            emit("URL", nu)
        nd = normalize_domain(f.url)
        if nd:
            emit("Domain", nd)

    # ---- Account / username ------------------------------------------
    platform = (payload.get("platform") or payload.get("service") or "").lower() or None
    user = payload.get("username") or payload.get("login") or payload.get("handle")
    if user:
        u = normalize_username(user, platform)
        if u:
            attrs = {"platform": platform}
            # Carry github bio for cross-link rules.
            if platform == "github":
                attrs["bio"] = payload.get("bio")
                attrs["blog"] = payload.get("blog")
            emit("Account", u, attrs)

    # ---- Person (from author/name fields) ----------------------------
    for key in ("author_name", "name", "full_name"):
        nm = normalize_name(payload.get(key))
        if nm:
            domain = normalize_domain(payload.get("repo") or payload.get("blog") or "")
            emit("Person", nm, {"domain": domain})

    # ---- Photo (gravatar etc.) ---------------------------------------
    if payload.get("hash") and f.collector == "gravatar":
        emit("Photo", f"gravatar:{payload['hash']}", {"gravatar_hash": payload["hash"]})

    # ---- Location / Document fallthrough -----------------------------
    if f.entity_type == "Location" and f.title:
        emit("Location", normalize_name(f.title) or f.title)
    if f.entity_type in {"Document", "Leak", "Breach"} and f.url:
        nu = normalize_url(f.url)
        if nu:
            emit("Document", nu)

    return out


# ---------------------------------------------------------------------------
# Union-Find
# ---------------------------------------------------------------------------

class _UF:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


# ---------------------------------------------------------------------------
# Clustering + merging
# ---------------------------------------------------------------------------

# Minimum confidence to accept a pairwise match link.
LINK_THRESHOLD = 0.60


def _cluster(candidates: list[Entity]) -> list[list[int]]:
    n = len(candidates)
    uf = _UF(n)
    # Pairwise — fine for typical case sizes (hundreds of findings).
    for i in range(n):
        for j in range(i + 1, n):
            score, _rule = best_match(candidates[i], candidates[j])
            if score >= LINK_THRESHOLD:
                uf.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return list(groups.values())


def _merge(group: list[Entity]) -> Entity:
    """Merge a cluster of candidates into a single Entity.

    The cluster may mix types (Email + Account + Person). We pick the most
    'specific' type as the canonical type, but keep all sources.
    """
    type_priority = {
        "Email": 7, "Phone": 7, "Account": 6, "URL": 5, "Domain": 5,
        "Photo": 4, "Person": 3, "Location": 2, "Document": 1,
    }
    primary = max(group, key=lambda e: (type_priority.get(e.type, 0), len(e.sources)))
    merged = Entity(
        case_id=primary.case_id,
        type=primary.type,  # type: ignore[arg-type]
        value=primary.value,
        attrs={},
    )
    seen_finding_ids: set[uuid.UUID] = set()
    for e in group:
        # Merge attrs (later wins on conflict, but never overwrites with None).
        for k, v in e.attrs.items():
            if v is not None and merged.attrs.get(k) in (None, ""):
                merged.attrs[k] = v
        # Track related secondary values under attrs.related[type] = [...]
        if e.value != merged.value:
            related = merged.attrs.setdefault("related", {})
            related.setdefault(e.type, [])
            if e.value not in related[e.type]:
                related[e.type].append(e.value)
        for s in e.sources:
            if s.raw_finding_id and s.raw_finding_id in seen_finding_ids:
                continue
            if s.raw_finding_id:
                seen_finding_ids.add(s.raw_finding_id)
            merged.sources.append(s)

    merged.score = combine_confidences(s.confidence for s in merged.sources)
    return merged


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def resolve(case_id: uuid.UUID) -> list[Entity]:
    """Resolve all findings for a case into entities + persist them.

    Returns the in-memory list (also written to DB).
    """
    async with session_scope() as s:
        rows = (
            await s.execute(select(DbFinding).where(DbFinding.case_id == case_id))
        ).scalars().all()

    candidates: list[Entity] = []
    for f in rows:
        candidates.extend(_candidates_from_finding(f))

    if not candidates:
        return []

    clusters = _cluster(candidates)
    entities = [_merge([candidates[i] for i in idxs]) for idxs in clusters]

    await _persist(case_id, entities)
    return entities


async def resolve_in_memory(findings: Iterable[DbFinding]) -> list[Entity]:
    """Same logic as `resolve` but operates on a provided iterable.
    Used by tests and by the orchestrator when DB roundtrip is undesired.
    """
    candidates: list[Entity] = []
    for f in findings:
        candidates.extend(_candidates_from_finding(f))
    if not candidates:
        return []
    clusters = _cluster(candidates)
    return [_merge([candidates[i] for i in idxs]) for idxs in clusters]


# ---------------------------------------------------------------------------
# Persistence (raw SQL — table is created by migration NNNN_entities.sql)
# ---------------------------------------------------------------------------

async def _persist(case_id: uuid.UUID, entities: list[Entity]) -> None:
    from sqlalchemy import text

    async with session_scope() as s:
        await s.execute(
            text("DELETE FROM entity_sources WHERE entity_id IN "
                 "(SELECT id FROM entities WHERE case_id = :cid)"),
            {"cid": str(case_id)},
        )
        await s.execute(
            text("DELETE FROM entities WHERE case_id = :cid"),
            {"cid": str(case_id)},
        )
        for e in entities:
            await s.execute(
                text(
                    "INSERT INTO entities (id, case_id, type, value, attrs, score) "
                    "VALUES (:id, :cid, :t, :v, CAST(:a AS JSONB), :sc)"
                ),
                {
                    "id": str(e.id),
                    "cid": str(case_id),
                    "t": e.type,
                    "v": e.value,
                    "a": _json(e.attrs),
                    "sc": e.score,
                },
            )
            for src in e.sources:
                await s.execute(
                    text(
                        "INSERT INTO entity_sources "
                        "(entity_id, finding_id, collector, confidence) "
                        "VALUES (:eid, :fid, :col, :conf)"
                    ),
                    {
                        "eid": str(e.id),
                        "fid": str(src.raw_finding_id) if src.raw_finding_id else None,
                        "col": src.collector,
                        "conf": src.confidence,
                    },
                )


def _json(obj: Any) -> str:
    import json
    return json.dumps(obj, default=str, ensure_ascii=False)
