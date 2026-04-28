"""Query expander (Wave 7).

Given a ``SearchInput``, produce a varied list of search queries that
SearXNG / dorks can run in parallel. The motivation: a single name like
"Juan García" misses transliterations, common nicknames, professional
contexts, and idiomatic formats ("Juan García Madrid abogado" is a much
sharper query than the bare name).

We do this once per case via a small Gemini Flash call (1 LLM round-trip,
~$0.001 / case). Results are cached in Redis for 7 days keyed by a hash of
the input fields, so re-running the same case is free.

Fail-soft: when the LLM is unavailable, we return a deterministic
fallback set built from the input alone. The system always gets *some*
expansion.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)


try:
    from app.llm.synthesis import _llm_call as _llm_call_real
except ImportError:  # pragma: no cover
    _llm_call_real = None  # type: ignore[assignment]


__all__ = [
    "EXPANSION_CACHE_TTL",
    "EXPANSION_CACHE_PREFIX",
    "MAX_QUERIES",
    "cache_key",
    "deterministic_fallback",
    "expand_queries",
]


EXPANSION_CACHE_TTL = 7 * 24 * 3600  # 7 days
EXPANSION_CACHE_PREFIX = "qx:v1:"
MAX_QUERIES = 25


def _input_signature(input_: Any) -> str:
    """Stable key over the fields the expander actually uses."""
    fields = ("full_name", "city", "country", "email", "username", "phone", "domain")
    parts = []
    for f in fields:
        v = getattr(input_, f, None)
        if v:
            parts.append(f"{f}={v}")
    return "|".join(parts)


def cache_key(input_: Any) -> str:
    sig = _input_signature(input_)
    digest = hashlib.sha256(sig.encode("utf-8")).hexdigest()[:24]
    return EXPANSION_CACHE_PREFIX + digest


def deterministic_fallback(input_: Any) -> list[str]:
    """Build a small set of variants without the LLM.

    Used when the LLM is unreachable so the rest of the pipeline still has
    something better than the raw single query.
    """
    out: list[str] = []
    name = (getattr(input_, "full_name", "") or "").strip()
    city = (getattr(input_, "city", "") or "").strip()
    country = (getattr(input_, "country", "") or "").strip()
    email = (getattr(input_, "email", "") or "").strip()
    username = (getattr(input_, "username", "") or "").strip().lstrip("@")
    domain = (getattr(input_, "domain", "") or "").strip()

    if name:
        out.append(name)
        # Surname-first / first-last variants
        parts = [p for p in re.split(r"\s+", name) if p]
        if len(parts) >= 2:
            out.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
            out.append(f"{parts[0]} {parts[-1]}")
        if city:
            out.append(f"{name} {city}")
            if country:
                out.append(f"{name} {city} {country}")
        out.append(f"\"{name}\"")
    if email:
        out.append(email)
        if "@" in email:
            local, dom = email.split("@", 1)
            out.append(local)
            out.append(f"\"@{dom}\"")
    if username:
        out.append(username)
        out.append(f"@{username}")
        if name:
            out.append(f"{username} {name}")
    if domain:
        out.append(domain)
        out.append(f"site:{domain}")

    # Dedup while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for q in out:
        if q and q not in seen:
            seen.add(q)
            deduped.append(q)
    return deduped[:MAX_QUERIES]


_PROMPT_TEMPLATE = """\
Genera consultas variadas para buscar OSINT sobre la persona descrita.
Devuelve EXACTAMENTE una lista JSON de strings (sin texto adicional, sin
Markdown). Mínimo 12 entradas, máximo {max_q}. Combina:

- Nombre + ciudad / país / sector profesional plausibles
- Apodos y diminutivos comunes en español
- Variantes con apellidos primero o entre comillas
- Email y dominios derivados (si se aporta email)
- Username con/sin "@", combinado con nombre real
- Dorks útiles (site:linkedin.com, site:github.com, intitle:CV, filetype:pdf)
- Transliteraciones obvias si el nombre tiene tildes o ñ

Datos del sujeto:
{subject}

Respuesta (JSON list, sin nada más):
"""


def _parse_list(raw: str) -> list[str] | None:
    """Tolerant JSON-list parser."""
    if not raw:
        return None
    m = re.search(r"\[\s\S]*?\]" if False else r"\[[\s\S]*\]", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    out = [str(x).strip() for x in data if str(x).strip()]
    return out or None


async def expand_queries(
    input_: Any,
    *,
    redis: Any | None = None,
    llm: str = "gemini",
) -> list[str]:
    """Return a deduplicated list of search queries for the given input.

    Cache hit returns immediately. Cache miss calls the LLM once and stores
    the result. On LLM failure we still return ``deterministic_fallback``.
    """
    sig = _input_signature(input_)
    if not sig:
        return []

    key = cache_key(input_)
    if redis is not None:
        try:
            cached = await redis.get(key)
            if cached:
                if isinstance(cached, bytes):
                    cached = cached.decode("utf-8")
                try:
                    data = json.loads(cached)
                    if isinstance(data, list):
                        return [str(x) for x in data][:MAX_QUERIES]
                except json.JSONDecodeError:
                    pass
        except Exception as exc:  # noqa: BLE001
            log.debug("query_expansion.cache_get_failed key=%s err=%s", key, exc)

    fallback = deterministic_fallback(input_)
    if _llm_call_real is None:
        return fallback

    prompt = _PROMPT_TEMPLATE.format(
        max_q=MAX_QUERIES,
        subject=sig.replace("|", "\n"),
    )
    try:
        raw, _model = await _llm_call_real(llm, prompt)
    except Exception as exc:  # noqa: BLE001
        log.debug("query_expansion.llm_failed err=%s", exc)
        return fallback

    parsed = _parse_list(raw or "")
    if not parsed:
        return fallback

    # Merge LLM output with the deterministic fallback to ensure the basics
    # are always covered, then dedup while preserving order.
    seen: set[str] = set()
    merged: list[str] = []
    for q in parsed + fallback:
        if q and q not in seen:
            seen.add(q)
            merged.append(q)
    final = merged[:MAX_QUERIES]

    if redis is not None and final:
        try:
            await redis.setex(key, EXPANSION_CACHE_TTL, json.dumps(final))
        except Exception as exc:  # noqa: BLE001
            log.debug("query_expansion.cache_set_failed key=%s err=%s", key, exc)

    return final
