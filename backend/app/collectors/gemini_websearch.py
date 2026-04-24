"""Gemini-powered web research collector.

Uses Gemini 2.5 with the Google Search grounding tool to actively browse the web
and produce structured findings about the subject. This adds knowledge no static
scraper can reach (fresh news, third-party mentions, cross-links) and naturally
handles homonyms because the LLM reads surrounding context.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from google import genai
from google.genai import types

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.schemas import SearchInput


PROMPT_TEMPLATE = """Actúa como analista OSINT. Usa Google Search para investigar a fondo a la
siguiente persona. Busca en español y en inglés. Prioriza **fuentes de los últimos 5 años**
y descarta homónimos:

Datos conocidos:
{subject_block}

Devuelve EXCLUSIVAMENTE un bloque JSON con este esquema:

```json
{{
  "candidates": [
    {{
      "title": "breve descripción (ej. 'Perfil LinkedIn', 'Noticia en El País', 'Charla TEDx')",
      "url": "https://...",
      "category": "profile|news|academic|corporate|social|document|image|other",
      "why_subject": "razón por la que crees que es el sujeto y no un homónimo",
      "confidence": "high|medium|low",
      "snippet": "texto breve citable de la fuente"
    }}
  ],
  "summary": "párrafo breve con lo más relevante encontrado",
  "aliases_detected": ["otros nombres o variantes que aparecen"],
  "photo_urls": ["URLs de fotos que probablemente sean del sujeto"],
  "locations": ["ciudades/países inferidos"],
  "employers": ["empresas u organizaciones asociadas"]
}}
```

Si no encuentras nada fiable devuelve `candidates: []` y explica en `summary`."""


def _subject_block(i: SearchInput) -> str:
    lines = []
    names = i.name_variants()
    if names:
        lines.append(f"- Nombres: {'; '.join(names)}")
    if i.email: lines.append(f"- Email: {i.email}")
    if i.phone: lines.append(f"- Teléfono: {i.phone}")
    if i.username: lines.append(f"- Username/alias digital: {i.username}")
    if i.linkedin_url: lines.append(f"- LinkedIn: {i.linkedin_url}")
    if i.domain: lines.append(f"- Dominio propio: {i.domain}")
    if i.city or i.country: lines.append(f"- Ubicación declarada: {i.city or ''} {i.country or ''}".strip())
    if i.extra_context: lines.append(f"- Contexto extra: {i.extra_context}")
    return "\n".join(lines) or "(sin datos)"


def _extract_json(text: str) -> dict | None:
    # Try code-fenced first
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Then first {...} block
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


@register
class GeminiWebResearchCollector(Collector):
    name = "gemini_websearch"
    category = "ai_research"
    needs = ("full_name", "birth_name", "aliases", "email", "username", "phone", "linkedin_url", "domain")
    timeout_seconds = 240
    description = "Gemini con grounding de Google Search: investigación web guiada por IA."

    def applicable(self, input: SearchInput) -> bool:
        if not get_settings().gemini_api_key:
            return False
        return bool(input.non_empty_fields())

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        s = get_settings()
        client = genai.Client(api_key=s.gemini_api_key)
        prompt = PROMPT_TEMPLATE.format(subject_block=_subject_block(input))

        try:
            resp = await client.aio.models.generate_content(
                model=s.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.15,
                    max_output_tokens=8000,
                ),
            )
        except Exception as e:  # SDK may raise many types; propagate as collector error
            raise RuntimeError(f"Gemini grounding error: {e}") from e

        text = resp.text or ""
        data = _extract_json(text) or {}

        # Emit candidates as findings
        for cand in (data.get("candidates") or [])[:50]:
            conf_raw = (cand.get("confidence") or "medium").lower()
            conf = {"high": 0.85, "medium": 0.6, "low": 0.4}.get(conf_raw, 0.55)
            yield Finding(
                collector=self.name,
                category=cand.get("category") or "ai_research",
                entity_type="GeminiCandidate",
                title=f"{cand.get('title','(sin título)')[:180]}",
                url=cand.get("url"),
                confidence=conf,
                payload={
                    "why_subject": cand.get("why_subject"),
                    "snippet": cand.get("snippet"),
                    "raw_confidence": conf_raw,
                },
            )

        # Alias / empleador / ubicación hints
        for alias in (data.get("aliases_detected") or [])[:10]:
            yield Finding(
                collector=self.name, category="name", entity_type="AliasHint",
                title=f"Alias detectado: {alias}", url=None, confidence=0.5,
                payload={"alias": alias},
            )
        for loc in (data.get("locations") or [])[:10]:
            yield Finding(
                collector=self.name, category="location", entity_type="LocationHint",
                title=f"Ubicación detectada: {loc}", url=None, confidence=0.5,
                payload={"location": loc},
            )
        for emp in (data.get("employers") or [])[:15]:
            yield Finding(
                collector=self.name, category="company", entity_type="EmployerHint",
                title=f"Empleador / organización: {emp}", url=None, confidence=0.55,
                payload={"employer": emp},
            )
        for pic in (data.get("photo_urls") or [])[:15]:
            yield Finding(
                collector=self.name, category="photo", entity_type="PhotoURL",
                title=f"Posible foto del sujeto", url=pic, confidence=0.5,
                payload={"image_url": pic, "source": "gemini_grounding"},
            )

        if data.get("summary"):
            yield Finding(
                collector=self.name, category="ai_research", entity_type="GeminiSummary",
                title="Resumen Gemini (Google grounding)",
                url=None, confidence=0.7,
                payload={"summary": data["summary"]},
            )
