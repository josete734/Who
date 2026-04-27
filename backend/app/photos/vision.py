"""Vision analysis pipeline (Wave 0 / A0.2).

Sends downloaded photo bytes to a local/remote Ollama multimodal model
(default ``llava:13b``) and parses the strict JSON it returns into a
small, predictable dict the rest of the pipeline can consume.

Public entry point::

    from app.photos.vision import analyze_photo
    result = await analyze_photo(image_bytes, settings=get_settings())

Design notes:
  * Errors NEVER raise — we always return a dict (possibly empty). The
    aggregator must remain robust even when the Ollama backend is down,
    misconfigured, slow or returns nonsense.
  * Output schema is deliberately small and parseable without an LLM:
      {ocr_text, landmarks[], inferred_city, inferred_country,
       vehicles[{make, model, plate}], attire, signage_language,
       est_time_of_day, indoor_outdoor}
  * The PROMPT mixes Spanish + English to bias the model toward strict
    JSON regardless of system language.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Strict prompt: the model is asked to return ONLY a JSON object with the
# documented keys. Mixed Spanish/English on purpose — local LLaVA tends to
# echo the dominant prompt language so we provide both signals.
PROMPT = (
    "Eres un analista OSINT. Analiza la imagen y devuelve EXCLUSIVAMENTE un "
    "objeto JSON valido (sin markdown, sin texto extra) con esta forma:\n"
    "{\n"
    '  "ocr_text": "<all readable text in image, joined with spaces, or empty string>",\n'
    '  "landmarks": ["<recognisable monuments, brands, buildings>"],\n'
    '  "inferred_city": "<city name if any signage / language / architecture suggests one, else empty>",\n'
    '  "inferred_country": "<country name in English, else empty>",\n'
    '  "vehicles": [{"make": "", "model": "", "plate": ""}],\n'
    '  "attire": "<short description: casual / suit / sport / uniform / etc>",\n'
    '  "signage_language": "<ISO-639-1 code or natural-language name; empty if no text>",\n'
    '  "est_time_of_day": "<one of: day, night, dawn, dusk, indoor, unknown>",\n'
    '  "indoor_outdoor": "<one of: indoor, outdoor, mixed, unknown>"\n'
    "}\n"
    "Rules / Reglas: Return STRICT JSON only. Use empty strings or empty "
    "arrays for unknowns. Do not invent data. Plates only if clearly "
    "legible. Landmarks only if recognisable globally."
)

_DEFAULT: dict[str, Any] = {
    "ocr_text": "",
    "landmarks": [],
    "inferred_city": "",
    "inferred_country": "",
    "vehicles": [],
    "attire": "",
    "signage_language": "",
    "est_time_of_day": "unknown",
    "indoor_outdoor": "unknown",
}


def _coerce(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw model dict into the canonical schema."""
    out = dict(_DEFAULT)
    if not isinstance(parsed, dict):
        return out
    for k in ("ocr_text", "inferred_city", "inferred_country", "attire",
              "signage_language", "est_time_of_day", "indoor_outdoor"):
        v = parsed.get(k)
        if isinstance(v, str):
            out[k] = v.strip()
    lm = parsed.get("landmarks")
    if isinstance(lm, list):
        out["landmarks"] = [str(x).strip() for x in lm if isinstance(x, (str, int, float)) and str(x).strip()]
    veh = parsed.get("vehicles")
    if isinstance(veh, list):
        clean: list[dict[str, str]] = []
        for v in veh:
            if not isinstance(v, dict):
                continue
            entry = {
                "make": str(v.get("make") or "").strip(),
                "model": str(v.get("model") or "").strip(),
                "plate": str(v.get("plate") or "").strip(),
            }
            if any(entry.values()):
                clean.append(entry)
        out["vehicles"] = clean
    return out


def _parse_response(raw: str) -> dict[str, Any]:
    """Tolerant JSON parse: try strict, then a regex slice between {...}."""
    if not raw or not isinstance(raw, str):
        return {}
    raw = raw.strip()
    # Strip code-fences if any.
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
    return {}


async def analyze_photo(image_bytes: bytes, *, settings: Any) -> dict[str, Any]:
    """Run Ollama vision on the given image. Never raises; returns ``{}`` on error."""
    if not image_bytes:
        return {}
    base = (getattr(settings, "ollama_base_url", "") or "").rstrip("/")
    model = getattr(settings, "ollama_vision_model", "") or "llava:13b"
    if not base:
        return {}

    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "prompt": PROMPT,
        "images": [b64],
        "stream": False,
        "format": "json",
    }
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = getattr(settings, "ollama_api_key", "") or ""
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{base}/api/generate"

    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                log.debug("ollama vision non-200 status=%s", r.status_code)
                return {}
            data = r.json()
    except Exception as exc:
        log.debug("ollama vision call failed: %s", exc)
        return {}

    # Ollama returns {"response": "<text>", ...} when stream=False.
    text = ""
    if isinstance(data, dict):
        text = data.get("response") or ""
        if not text and isinstance(data.get("message"), dict):
            text = data["message"].get("content") or ""

    parsed = _parse_response(text)
    if not parsed:
        return {}
    return _coerce(parsed)
