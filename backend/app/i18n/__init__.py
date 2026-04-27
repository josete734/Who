"""Lightweight i18n helper.

Public API:
    t(key, lang='es', **vars) -> str
    detect_lang(accept_language: str | None, default='es') -> str
    register_t(env)  # registers Jinja filter `t`

Fallback chain per requested language:
    ca -> es -> en
    es -> en
    en -> (none)
    <other> -> es -> en

Locale JSON files live in `backend/app/i18n/locales/{es,en,ca}.json`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCALES_DIR = Path(__file__).parent / "locales"
_SUPPORTED = ("es", "en", "ca")
_DEFAULT = "es"

# Per-language fallback chains (ordered, primary first).
_FALLBACKS: dict[str, tuple[str, ...]] = {
    "ca": ("ca", "es", "en"),
    "es": ("es", "en"),
    "en": ("en",),
}

_cache: dict[str, dict[str, str]] = {}


def _load(lang: str) -> dict[str, str]:
    if lang in _cache:
        return _cache[lang]
    path = _LOCALES_DIR / f"{lang}.json"
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except Exception as e:  # pragma: no cover
        logger.warning("i18n: failed to load %s: %s", path, e)
        data = {}
    _cache[lang] = data
    return data


def _chain(lang: str) -> tuple[str, ...]:
    return _FALLBACKS.get(lang, (lang, "es", "en"))


def t(key: str, lang: str = _DEFAULT, **vars: Any) -> str:
    """Translate `key` for `lang` with fallback. Missing key returns the key."""
    lang = (lang or _DEFAULT).lower()
    for code in _chain(lang):
        bundle = _load(code)
        if key in bundle:
            value = bundle[key]
            if vars:
                try:
                    return value.format(**vars)
                except (KeyError, IndexError, ValueError):
                    return value
            return value
    return key


def detect_lang(accept_language: str | None, default: str = _DEFAULT) -> str:
    """Parse `Accept-Language` header and pick a supported language."""
    if not accept_language:
        return default
    best_lang = default
    best_q = -1.0
    for part in accept_language.split(","):
        token = part.strip()
        if not token:
            continue
        if ";" in token:
            tag, *params = token.split(";")
            q = 1.0
            for p in params:
                p = p.strip()
                if p.startswith("q="):
                    try:
                        q = float(p[2:])
                    except ValueError:
                        q = 0.0
        else:
            tag, q = token, 1.0
        tag = tag.strip().lower()
        primary = tag.split("-", 1)[0]
        if primary in _SUPPORTED and q > best_q:
            best_q = q
            best_lang = primary
    return best_lang


def register_t(env) -> None:
    """Register the `t` Jinja filter on a Jinja Environment."""
    def _filter(key: str, lang: str = _DEFAULT, **vars: Any) -> str:
        return t(key, lang=lang, **vars)

    env.filters["t"] = _filter
    env.globals.setdefault("t", t)


__all__ = ["t", "detect_lang", "register_t"]
