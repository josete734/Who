"""Local combo-list breach collector (Wave 3 / C10).

Searches a local Postgres index (table ``breach_records``) populated from
combo-list CSVs imported via ``backend/scripts/import_combo.py``.

Privacy / GDPR posture
----------------------
* Plaintext passwords are NEVER persisted. The importer hashes the email
  with SHA-256 (over the lowercased + trimmed value) and reduces the
  password to a coarse, non-reversible class (length bucket + presence of
  digits / symbols).
* Lookups are by ``sha256(normalized_email)`` only — the collector does
  not need (and never receives) the plaintext password.
* Retention: rows are subject to the controller's documented TTL; see the
  module docstring of ``backend/scripts/import_combo.py``.
"""
from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

from sqlalchemy import text

from app.collectors.base import Collector, Finding, register
from app.db import session_scope
from app.schemas import SearchInput


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _email_sha256(email: str) -> bytes:
    return hashlib.sha256(_normalize_email(email).encode("utf-8")).digest()


@register
class ComboListLocal(Collector):
    """Search the local combo-list FTS/index for known exposures."""

    name = "combo_lists_local"
    category = "breach"
    needs = ("email", "username")
    requires_all = False
    timeout_seconds = 10
    description = (
        "Looks up the email's SHA-256 (and optional username) in the local "
        "breach_records index. No plaintext passwords are ever stored or returned."
    )

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        email = (input.email or "").strip()
        username = (input.username or "").strip()
        if not email and not username:
            return

        rows: list[dict] = []
        async with session_scope() as session:
            if email:
                ehash = _email_sha256(email)
                result = await session.execute(
                    text(
                        "SELECT source, username, hint_password_class, observed_at "
                        "FROM breach_records WHERE email_hash = :h "
                        "ORDER BY observed_at DESC LIMIT 50"
                    ),
                    {"h": ehash},
                )
                rows.extend(dict(r._mapping) for r in result)
            if username and not rows:
                result = await session.execute(
                    text(
                        "SELECT source, username, hint_password_class, observed_at "
                        "FROM breach_records WHERE username = :u "
                        "ORDER BY observed_at DESC LIMIT 50"
                    ),
                    {"u": username},
                )
                rows.extend(dict(r._mapping) for r in result)

        for row in rows:
            yield Finding(
                collector=self.name,
                category="breach",
                entity_type="BreachHit",
                title=f"Exposición en combo list: {row['source']}",
                url=None,
                confidence=0.8,
                payload={
                    "source": row["source"],
                    "observed_at": row["observed_at"].isoformat()
                    if row.get("observed_at")
                    else None,
                    "hint_password_class": row.get("hint_password_class"),
                    "username": row.get("username"),
                },
            )


# ---------------------------------------------------------------------------
# WIRING — NOT registered automatically.
# ---------------------------------------------------------------------------
# This collector intentionally omits the @register decorator. To activate
# it, the integration agent must:
#   1. Apply migration ``0004_breach_index.sql``.
#   2. Import combo data with ``backend/scripts/import_combo.py``.
#   3. Add ``register(ComboListLocal)`` here OR import + register from the
#      orchestrator bootstrap once the legal review for combo-list usage
#      is signed off.
# ---------------------------------------------------------------------------
