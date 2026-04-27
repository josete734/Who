"""Dehashed v2 breach search collector (paid API, optional).

Looks up email / username / phone / domain across the Dehashed breach
corpus via the v2 search endpoint. The API requires an account email
plus an API key, which are pulled from runtime settings:

    DEHASHED_EMAIL     -- account email used as the basic-auth user
    DEHASHED_API_KEY   -- API key (basic-auth password OR bearer token)

If either credential is missing the collector returns no findings (it is
strictly opt-in). 401 (bad credentials) and 402 (out of credits / no
subscription) are also treated as "silently empty" so a missing or
expired key never poisons a case.

Sensitive-data hygiene
----------------------
Dehashed returns plaintext passwords for many breaches. We never log
or persist the raw password. Instead each Finding stores:

    payload.value = "<first 2 chars>***"     # for the UI / list views
    payload.evidence.password_sha256 = sha256(password)

so analysts can correlate identical leaked passwords across breaches
without the tool itself becoming a credential dump.

Registration is intentionally NOT done here -- the integration agent
will register this collector explicitly (see WIRING comments below).
"""
from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.collectors.base import Collector, Finding, register
from app.dynamic_settings import get_runtime
from app.http_util import client
from app.schemas import SearchInput

# WIRING: add the following two fields to backend/app/config.py Settings:
# WIRING:   DEHASHED_EMAIL: str = ""
# WIRING:   DEHASHED_API_KEY: str = ""

# WIRING: register this collector in backend/app/collectors/__init__.py:
# WIRING:   from app.collectors.dehashed import DehashedCollector  # noqa: F401
# WIRING:   register(DehashedCollector)


_DEHASHED_URL = "https://api.dehashed.com/v2/search"
# Conservative cap (Dehashed default page size is 100, hard max 10k via paging).
_PAGE_SIZE = 100
_MAX_FINDINGS = 50


def _mask_password(pw: str) -> str:
    if not pw:
        return ""
    if len(pw) <= 2:
        return pw[:1] + "***"
    return pw[:2] + "***"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()


@register
class DehashedCollector(Collector):
    name = "dehashed"
    category = "breach"
    needs = ("email", "username", "phone", "domain")
    timeout_seconds = 25
    max_retries = 1
    description = "Dehashed v2 breach search (paid). Maps leaks to redacted findings."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        rt = await get_runtime()
        account = (rt.get("DEHASHED_EMAIL") or "").strip()
        api_key = (rt.get("DEHASHED_API_KEY") or "").strip()
        if not account or not api_key:
            # No key configured -> opt-out, emit nothing.
            return

        # Build the list of (email-clause OR None) probes. We issue a separate
        # query per email so each finding can be tagged with the concrete one.
        emails = input.emails() or [None]

        # Non-email clauses are shared across probes.
        shared_clauses: list[str] = []
        if input.username:
            shared_clauses.append(f'username:"{input.username.strip()}"')
        if input.phone:
            shared_clauses.append(f'phone:"{input.phone.strip()}"')
        if input.domain:
            shared_clauses.append(f'domain:"{input.domain.strip()}"')

        # Dehashed v2 accepts either basic-auth (email:api_key) or a bearer
        # token. We send both to be tolerant of doc drift; the server picks one.
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        emitted = 0
        seen_probe_keys: set[str] = set()
        for probe_email in emails:
            clauses = list(shared_clauses)
            if probe_email:
                clauses.append(f'email:"{probe_email.strip()}"')
            if not clauses:
                continue
            query = " OR ".join(clauses)
            # Avoid duplicate probes if shared_clauses make queries identical
            # when no emails were configured.
            if query in seen_probe_keys:
                continue
            seen_probe_keys.add(query)

            body = {"query": query, "size": _PAGE_SIZE, "page": 1}

            try:
                async with client(timeout=20, headers=headers) as c:
                    r = await c.post(
                        _DEHASHED_URL,
                        json=body,
                        auth=(account, api_key),
                    )
            except httpx.HTTPError:
                continue

            # 401 = bad creds, 402 = no subscription / out of credits, 429 = rate
            # limited. Any of these → emit nothing, do not raise.
            if r.status_code in (401, 402, 403, 429):
                return
            if r.status_code != 200:
                continue

            try:
                data = r.json()
            except ValueError:
                continue

            entries = data.get("entries") or data.get("data") or []
            if not isinstance(entries, list):
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if emitted >= _MAX_FINDINGS:
                    return

                finding = _entry_to_finding(self.name, entry, probe_email=probe_email)
                if finding is None:
                    continue
                yield finding
                emitted += 1


def _entry_to_finding(collector_name: str, entry: dict[str, Any], probe_email: str | None = None) -> Finding | None:
    breach = (
        entry.get("database_name")
        or entry.get("obtained_from")
        or entry.get("breach")
        or "unknown"
    )
    email = entry.get("email") or ""
    username = entry.get("username") or ""
    name = entry.get("name") or ""
    address = entry.get("address") or ""
    phone = entry.get("phone") or ""
    raw_hash = entry.get("hashed_password") or entry.get("hash") or ""
    raw_pw = entry.get("password") or ""

    # Anchor value: prefer the most identifying non-secret field.
    anchor = email or username or phone or name or address or breach
    if not anchor and not raw_pw and not raw_hash:
        return None

    masked_value = anchor
    evidence: dict[str, Any] = {
        "breach_name": breach,
        "email": email or None,
        "username": username or None,
        "name": name or None,
        "address": address or None,
        "phone": phone or None,
    }

    if raw_pw:
        evidence["password_sha256"] = _sha256(raw_pw)
        evidence["password_masked"] = _mask_password(raw_pw)
        # The on-screen value highlights that a password leaked while masking it.
        masked_value = f"{anchor} :: pw={_mask_password(raw_pw)}"
    if raw_hash:
        evidence["hashed_password"] = raw_hash  # already a hash, safe to keep
        evidence["hash_algo"] = entry.get("hash_type") or None

    # Strip empty keys for cleaner payloads.
    evidence = {k: v for k, v in evidence.items() if v}

    payload: dict[str, Any] = {
        "value": masked_value,
        "breach_name": breach,
        "evidence": evidence,
    }
    # Tag the finding with the concrete email used in the probe so downstream
    # consumers (pivot extractor, UI) can disambiguate primary vs secondary.
    concrete_email = email or probe_email
    if concrete_email:
        payload["email"] = concrete_email

    return Finding(
        collector=collector_name,
        category="breach",
        entity_type="DehashedRecord",
        title=f"Dehashed: {breach}",
        url=None,
        confidence=0.85,
        payload=payload,
    )
