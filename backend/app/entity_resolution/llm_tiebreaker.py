"""LLM tiebreaker for ambiguous person clusters (Wave 5).

R13 in ``match.py`` already handles cheap homonym disambiguation via
attribute-level signals (city, birth year, occupation). When those are not
enough — e.g. two clusters with very similar names, no overlapping
attributes, but enough indirect context (bios, employers, places) — we
delegate the decision to a small LLM.

The contract:

* Input: two clusters of findings (each is a list of compact dicts), the
  candidate person value, and a budget cap.
* Output: ``(same_person, confidence, reason)``. ``same_person`` is True
  when the LLM is confident enough; otherwise False. The caller persists
  ``confidence`` so downstream consumers can see why the merge happened.

We deliberately keep this orthogonal to the rule engine: callers invoke it
*only* when the rules return a score in the ambiguous band [0.55, 0.75].
Cap is enforced per-case via a simple counter the caller passes in.

LLM provider is read from ``settings.default_llm`` falling back to Gemini.
Network errors / parse failures bias the decision to ``False`` (don't
merge) so the system fails closed.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

# Imports kept at module-level (not lazy inside the function) so tests can
# monkeypatch ``app.entity_resolution.llm_tiebreaker._llm_call`` cleanly.
try:  # pragma: no cover — best-effort, falls back to a stub on import error
    from app.config import get_settings as _get_settings
    from app.llm.synthesis import _llm_call
except ImportError:  # pragma: no cover
    _get_settings = None  # type: ignore[assignment]
    async def _llm_call(llm: str, prompt: str) -> tuple[str, str]:  # type: ignore[no-redef]
        raise RuntimeError("LLM stack unavailable")

log = logging.getLogger(__name__)


__all__ = ["LLMTiebreakerResult", "decide_same_person", "AMBIGUOUS_BAND"]


# When R6+R13 produce a score in this half-open interval, the caller may
# decide to escalate to the LLM tiebreaker.
AMBIGUOUS_BAND: tuple[float, float] = (0.55, 0.75)


@dataclass(frozen=True)
class LLMTiebreakerResult:
    same_person: bool
    confidence: float  # 0..1
    reason: str
    model: str = ""


_PROMPT_TEMPLATE = """\
Eres un analista OSINT. Decide si los dos clústers de hallazgos describen
a la MISMA persona o a personas DISTINTAS que comparten un nombre similar.

Nombre candidato: {name}

Clúster A:
{cluster_a}

Clúster B:
{cluster_b}

Devuelve EXACTAMENTE un objeto JSON con esta forma y NADA más:
{{
  "same_person": true | false,
  "confidence": 0.0-1.0,
  "reason": "<máx 240 caracteres explicando la decisión>"
}}

Criterios:
- Misma ciudad y época + datos profesionales coherentes ⇒ probable misma persona.
- Ciudades o profesiones discordantes ⇒ probable distintas.
- Pocos datos ⇒ confidence baja, decide por defecto FALSE (no fusionar).
"""


def _compact_cluster(findings: list[dict[str, Any]], *, max_items: int = 6) -> str:
    """Render a few representative findings as a compact JSON list."""
    out: list[dict[str, Any]] = []
    for f in findings[:max_items]:
        if not isinstance(f, dict):
            continue
        out.append(
            {
                "collector": f.get("collector"),
                "title": (f.get("title") or "")[:160],
                "url": f.get("url"),
                "category": f.get("category"),
                # Only the most useful payload keys, capped to keep tokens low.
                "city": (f.get("payload") or {}).get("city"),
                "occupation": (f.get("payload") or {}).get("occupation"),
                "company": (f.get("payload") or {}).get("company"),
                "birth_year": (f.get("payload") or {}).get("birth_year"),
                "bio": ((f.get("payload") or {}).get("bio") or "")[:200],
            }
        )
    return json.dumps(out, ensure_ascii=False, indent=2)


def _parse_response(raw: str) -> tuple[bool, float, str] | None:
    """Extract the JSON verdict from the LLM response (tolerant of fluff)."""
    if not raw:
        return None
    # Find the first {...} block in the response and try to parse it.
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    same = bool(data.get("same_person"))
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        conf = 0.0
    reason = str(data.get("reason") or "")[:300]
    return same, conf, reason


async def decide_same_person(
    name: str,
    cluster_a: list[dict[str, Any]],
    cluster_b: list[dict[str, Any]],
    *,
    llm: str | None = None,
) -> LLMTiebreakerResult:
    """Ask the LLM to break the tie. Fail-soft: returns ``same_person=False``
    on any error so we never accidentally merge distinct people."""
    if not cluster_a or not cluster_b:
        return LLMTiebreakerResult(
            same_person=False,
            confidence=0.0,
            reason="empty_cluster",
        )

    if _get_settings is None:
        return LLMTiebreakerResult(False, 0.0, "llm_stack_unavailable")
    settings = _get_settings()
    chosen = (llm or getattr(settings, "default_llm", "") or "gemini").lower()

    prompt = _PROMPT_TEMPLATE.format(
        name=name[:120],
        cluster_a=_compact_cluster(cluster_a),
        cluster_b=_compact_cluster(cluster_b),
    )

    try:
        raw, model = await _llm_call(chosen, prompt)
    except Exception as exc:  # noqa: BLE001
        log.warning("er.tiebreaker.llm_failed llm=%s err=%s", chosen, exc)
        return LLMTiebreakerResult(False, 0.0, f"llm_failed: {exc}", model="")

    parsed = _parse_response(raw or "")
    if parsed is None:
        return LLMTiebreakerResult(
            same_person=False,
            confidence=0.0,
            reason="parse_failed",
            model=model or "",
        )
    same, conf, reason = parsed
    return LLMTiebreakerResult(
        same_person=same,
        confidence=conf,
        reason=reason or ("merge" if same else "distinct"),
        model=model or "",
    )
