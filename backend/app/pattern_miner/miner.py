"""Orchestration: produce candidates, verify, persist verified findings."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any

from app.pattern_miner.email_patterns import (
    extract_domains_from_borme,
    generate_email_variants,
)
from app.pattern_miner.username_variants import generate_username_variants
from app.pattern_miner.verifier import VerifierResult, verify_many


@dataclass
class MineResult:
    case_id: str | None
    usernames: list[VerifierResult]
    emails: list[VerifierResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "usernames": [_vr_dict(v) for v in self.usernames],
            "emails": [_vr_dict(v) for v in self.emails],
        }


def _vr_dict(v: VerifierResult) -> dict[str, Any]:
    return {
        "candidate": v.candidate,
        "kind": v.kind,
        "score": v.score,
        "verified": v.verified,
        "confirmations": v.confirmations,
        "payload": v.payload,
    }


async def mine_patterns(
    *,
    case_id: str | uuid.UUID | None = None,
    full_name: str | None = None,
    birth_name: str | None = None,
    aliases: str | None = None,
    domains: list[str] | None = None,
    borme_payloads: list[dict] | None = None,
    collector_registry: Any = None,
    persist: bool = True,
    enable_network: bool = True,
    max_username_variants: int = 120,
    max_email_variants: int = 120,
) -> MineResult:
    """Generate variants, verify them, and (optionally) persist verified ones as findings."""
    # 1. Username variants
    usernames = generate_username_variants(
        full_name=full_name,
        birth_name=birth_name,
        aliases=aliases,
        max_variants=max_username_variants,
    )

    # 2. Email variants. Auto-detect company domains from BORME hits if requested.
    detected = extract_domains_from_borme(borme_payloads or [])
    all_domains = list({*(domains or []), *detected})
    emails: list[str] = []
    if all_domains:
        emails = generate_email_variants(
            all_domains,
            full_name=full_name,
            birth_name=birth_name,
            aliases=aliases,
            max_variants=max_email_variants,
        )

    # 3. Verify
    candidates: list[tuple[str, str]] = [(u, "username") for u in usernames] + \
                                       [(e, "email") for e in emails]
    results = await verify_many(
        candidates,
        collector_registry=collector_registry,
        enable_network=enable_network,
    )
    user_res = [r for r in results if r.kind == "username"]
    mail_res = [r for r in results if r.kind == "email"]

    # 4. Persist verified
    if persist and case_id is not None:
        try:
            await _persist_findings(case_id, [r for r in results if r.verified])
        except Exception:
            # Persistence is best-effort — never break the pipeline.
            pass

    return MineResult(
        case_id=str(case_id) if case_id else None,
        usernames=sorted(user_res, key=lambda r: -r.score),
        emails=sorted(mail_res, key=lambda r: -r.score),
    )


async def _persist_findings(case_id: str | uuid.UUID, verified: list[VerifierResult]) -> None:
    """Persist verified candidates as Finding rows. Imported lazily so unit tests
    don't need a DB."""
    if not verified:
        return
    from app.collectors.base import Finding as FindingDC  # dataclass
    from app.db import Finding as FindingRow, session_scope

    cid = uuid.UUID(str(case_id)) if not isinstance(case_id, uuid.UUID) else case_id

    async with session_scope() as s:
        for v in verified:
            kind_cat = "derived_username" if v.kind == "username" else "derived_email"
            entity = "DerivedUsername" if v.kind == "username" else "DerivedEmail"
            f_dc = FindingDC(
                collector="pattern_miner",
                category=kind_cat,
                entity_type=entity,
                title=v.candidate,
                url=None,
                confidence=v.score,
                payload={
                    "confirmations": v.confirmations,
                    **v.payload,
                },
            )
            s.add(FindingRow(
                case_id=cid,
                collector=f_dc.collector,
                category=f_dc.category,
                entity_type=f_dc.entity_type,
                title=f_dc.title,
                url=f_dc.url,
                confidence=f_dc.confidence,
                payload=f_dc.payload,
                fingerprint=f_dc.fingerprint(),
            ))
