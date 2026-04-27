"""STIX 2.1 bundle exporter."""
from __future__ import annotations

import uuid
from typing import Any


async def export_stix(case_id: uuid.UUID | str, db: Any) -> dict:
    """Build a STIX 2.1 bundle for a case.

    Returns a python ``dict`` (already serialized via ``stix2`` so the
    spec_version / id fields are guaranteed valid).
    """
    import stix2  # type: ignore
    from sqlalchemy import select

    from app.db import Case, Finding  # noqa: F401

    if isinstance(case_id, str):
        try:
            case_id = uuid.UUID(case_id)
        except ValueError:
            pass

    case = await db.get(Case, case_id)
    if case is None:
        raise ValueError(f"case {case_id} not found")

    res = await db.execute(
        select(Finding).where(Finding.case_id == case_id).order_by(Finding.created_at.asc())
    )
    findings = list(res.scalars().all())

    identity = stix2.Identity(
        name=f"OSINT Case {case.title}",
        identity_class="organization",
        description=f"case_id={case.id}; legal_basis={case.legal_basis or ''}",
    )

    objects: list[Any] = [identity]
    for f in findings:
        payload = dict(getattr(f, "payload", {}) or {})
        payload.setdefault("collector", getattr(f, "collector", ""))
        payload.setdefault("category", getattr(f, "category", ""))
        payload.setdefault("title", getattr(f, "title", ""))
        if getattr(f, "url", None):
            payload.setdefault("url", f.url)

        # Pick a SCO matching the finding category when possible.
        cat = (getattr(f, "category", "") or "").lower()
        title = getattr(f, "title", "") or "n/a"
        sco: Any
        if cat == "email":
            sco = stix2.EmailAddress(value=title)
        elif cat == "domain":
            sco = stix2.DomainName(value=title)
        elif cat == "ip":
            sco = stix2.IPv4Address(value=title)
        elif cat == "url" or getattr(f, "url", None):
            sco = stix2.URL(value=getattr(f, "url", title))
        else:
            sco = stix2.URL(value=f"x-osint://{cat or 'unknown'}/{title}")

        observed = stix2.ObservedData(
            first_observed=f.created_at,
            last_observed=f.created_at,
            number_observed=1,
            created_by_ref=identity.id,
            object_refs=[sco.id],
            allow_custom=True,
            x_osint_payload=payload,
        )
        rel = stix2.Relationship(
            source_ref=identity.id,
            target_ref=observed.id,
            relationship_type="related-to",
        )
        objects.extend([sco, observed, rel])

    bundle = stix2.Bundle(objects=objects, allow_custom=True)
    # stix2 objects expose .serialize(); use json round-trip for a plain dict.
    import json as _json
    return _json.loads(bundle.serialize())
