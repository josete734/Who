"""Tests for backend.app.i18n: fallback chain, var interpolation, helpers."""

from __future__ import annotations

import pytest
from jinja2 import Environment

from backend.app.i18n import detect_lang, register_t, t


def test_es_direct_lookup():
    assert t("login.title", lang="es") == "Acceder"


def test_en_direct_lookup():
    assert t("login.title", lang="en") == "Sign in"


def test_ca_direct_lookup():
    assert t("login.title", lang="ca") == "Accedir"


def test_ca_falls_back_to_es():
    # "index.hero.desc" exists in es+en but not in ca -> should return ES value.
    assert t("index.hero.desc", lang="ca").startswith("Agregación")


def test_ca_falls_back_to_en_when_missing_in_es_only_unlikely():
    # Force chain by asking a key only present in en (none here), use unknown lang.
    # 'xx' chain: xx -> es -> en. Unknown key returns the key itself.
    assert t("does.not.exist", lang="xx") == "does.not.exist"


def test_es_falls_back_to_en_for_es_missing_key():
    # If key is missing from es, es chain falls back to en.
    # We simulate by patching: use a key only in en.
    from backend.app import i18n as mod
    mod._cache.clear()
    mod._cache["es"] = {}
    mod._cache["en"] = {"only.en": "EN-VALUE"}
    assert t("only.en", lang="es") == "EN-VALUE"
    mod._cache.clear()  # reset for other tests


def test_missing_key_returns_key():
    assert t("totally.unknown.key", lang="es") == "totally.unknown.key"


def test_var_interpolation_es():
    assert t("common.hello", lang="es", name="Jose") == "Hola Jose"


def test_var_interpolation_en():
    assert t("common.hello", lang="en", name="Jose") == "Hello Jose"


def test_var_interpolation_missing_var_returns_unformatted():
    # When a var is missing, we return the raw template (graceful).
    out = t("common.hello", lang="es")
    assert "{name}" in out


def test_default_lang_is_es():
    assert t("login.title") == "Acceder"


def test_detect_lang_none():
    assert detect_lang(None) == "es"


def test_detect_lang_empty():
    assert detect_lang("") == "es"


def test_detect_lang_simple_en():
    assert detect_lang("en-US,en;q=0.9") == "en"


def test_detect_lang_catalan():
    assert detect_lang("ca-ES,ca;q=0.9,es;q=0.5") == "ca"


def test_detect_lang_quality_priority():
    # English has higher q than Spanish here.
    assert detect_lang("es;q=0.3, en;q=0.9") == "en"


def test_detect_lang_unsupported_falls_back_to_default():
    assert detect_lang("ja-JP,ja;q=0.9") == "es"


def test_detect_lang_custom_default():
    assert detect_lang("ja", default="en") == "en"


def test_register_t_jinja_filter():
    env = Environment()
    register_t(env)
    assert "t" in env.filters
    tpl = env.from_string("{{ 'login.submit' | t('en') }}")
    assert tpl.render() == "Enter"


def test_register_t_jinja_global():
    env = Environment()
    register_t(env)
    tpl = env.from_string("{{ t('common.hello', 'en', name='Ada') }}")
    assert tpl.render() == "Hello Ada"


def test_jinja_filter_fallback_ca_to_es():
    env = Environment()
    register_t(env)
    tpl = env.from_string("{{ 'index.hero.desc' | t('ca') }}")
    out = tpl.render()
    assert out.startswith("Agregación")  # fell back to es
