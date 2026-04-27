"""RapidAPI collectors.

All share the env var RAPIDAPI_KEY. If missing, they mark themselves non-applicable.
"""
from __future__ import annotations

import phonenumbers
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.http_util import client
from app.schemas import SearchInput


def _rapid_client(host: str) -> httpx.AsyncClient:
    key = get_settings().rapidapi_key
    return client(
        timeout=25,
        headers={
            "x-rapidapi-key": key,
            "x-rapidapi-host": host,
            "Content-Type": "application/json",
        },
    )


def _digits_only(phone: str) -> str:
    try:
        num = phonenumbers.parse(phone, None if phone.startswith("+") else "ES")
        return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164).lstrip("+")
    except Exception:
        return phone.strip().lstrip("+")


# --------------------------------------------------------------------------
# 1) WhatsApp OSINT — POST /bizos with {"phone": "<digits>"}
# --------------------------------------------------------------------------
@register
class RapidAPIWhatsApp(Collector):
    name = "rapidapi_whatsapp"
    category = "phone"
    needs = ("phone",)
    timeout_seconds = 25
    description = "WhatsApp OSINT via RapidAPI (business/profile data for a phone number)."

    def applicable(self, input: SearchInput) -> bool:
        return bool(input.phone) and bool(get_settings().rapidapi_key)

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.phone
        phone = _digits_only(input.phone)
        async with _rapid_client("whatsapp-osint.p.rapidapi.com") as c:
            try:
                r = await c.post(
                    "https://whatsapp-osint.p.rapidapi.com/bizos",
                    json={"phone": phone},
                )
            except httpx.HTTPError as e:
                raise RuntimeError(f"WhatsApp OSINT request failed: {e}") from e

        # AUDIT FIX: graceful skip on auth/rate-limit/5xx instead of raising — keeps
        # the case usable when the RapidAPI subscription is missing or saturated.
        if r.status_code in (401, 403, 429) or r.status_code >= 500 or r.status_code != 200:
            return

        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:2000]}

        # AUDIT FIX: endpoint sometimes returns a list (e.g. [] when not registered) —
        # normalise to dict before calling .get to avoid AttributeError.
        if isinstance(data, list):
            data = {"results": data} if data else {}
        if not isinstance(data, dict):
            return
        exists = bool(data) and not data.get("error")
        if not exists:
            return

        # Try to extract common fields; be defensive.
        biz = data.get("biz") or data.get("business") or {}
        profile = data.get("profile") or {}
        about = profile.get("about") or data.get("status") or data.get("about")
        name = profile.get("name") or data.get("name") or biz.get("name")
        pic = profile.get("picture") or data.get("picture") or profile.get("photo")

        yield Finding(
            collector=self.name,
            category="phone",
            entity_type="WhatsAppProfile",
            title=f"WhatsApp perfil ({name or phone})",
            url=f"https://wa.me/{phone}",
            confidence=0.9,
            payload={
                "phone": phone,
                "name": name,
                "about": about,
                "picture": pic,
                "business": biz or None,
                "raw": data,
            },
        )


# --------------------------------------------------------------------------
# 2) Social Media Scanner — POST /check with {"input": "<email|phone|user>"}
# --------------------------------------------------------------------------
@register
class RapidAPISocialScanner(Collector):
    name = "rapidapi_socialscan"
    category = "social"
    # Applicable if ANY of these are present
    needs = ("email", "phone", "username")
    timeout_seconds = 45
    description = "Social Media Scanner via RapidAPI (email/phone/username → accounts)."

    def applicable(self, input: SearchInput) -> bool:
        if not get_settings().rapidapi_key:
            return False
        return bool(input.email or input.phone or input.username)

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        inputs: list[tuple[str, str]] = []
        if input.email:
            inputs.append(("email", input.email))
        if input.phone:
            inputs.append(("phone", _digits_only(input.phone)))
        if input.username:
            inputs.append(("username", input.username.lstrip("@")))

        async with _rapid_client("social-media-scanner1.p.rapidapi.com") as c:
            for kind, value in inputs:
                try:
                    r = await c.post(
                        "https://social-media-scanner1.p.rapidapi.com/check",
                        json={"input": value},
                    )
                except httpx.HTTPError:
                    continue
                # AUDIT FIX: don't raise on 429 — log via skip and continue with
                # remaining inputs so partial coverage still produces findings.
                if r.status_code in (401, 403, 429):
                    continue
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except ValueError:
                    continue

                # Expected shape: {"results":[{"platform":"...", "exists":true, "url":"..."}, ...]}
                items = []
                if isinstance(data, dict):
                    items = data.get("results") or data.get("accounts") or data.get("platforms") or []
                    if not items and "services" in data:
                        items = data["services"]
                elif isinstance(data, list):
                    items = data

                for it in items[:200]:
                    if not isinstance(it, dict):
                        continue
                    exists = it.get("exists") or it.get("found") or it.get("registered")
                    if exists is False:
                        continue
                    platform = it.get("platform") or it.get("service") or it.get("name") or "?"
                    p_url = it.get("url") or it.get("profile_url") or it.get("link")
                    yield Finding(
                        collector=self.name,
                        category="social",
                        entity_type="SocialAccount",
                        title=f"{platform} (vía {kind}={value[:40]})",
                        url=p_url,
                        confidence=0.75,
                        payload={"input_kind": kind, "input_value": value, "raw": it},
                    )

                if not items:
                    # Still emit a summary so the collector shows activity.
                    yield Finding(
                        collector=self.name,
                        category="social",
                        entity_type="SocialScanSummary",
                        title=f"Social Media Scanner: sin cuentas para {kind}={value[:40]}",
                        url=None,
                        confidence=0.4,
                        payload={"input_kind": kind, "input_value": value, "raw": data},
                    )


# --------------------------------------------------------------------------
# 3) Email verifier — "mailcheck.p.rapidapi.com" freemium
# --------------------------------------------------------------------------
@register
class RapidAPIEmailMailCheck(Collector):
    name = "rapidapi_mailcheck"
    category = "email"
    needs = ("email",)
    timeout_seconds = 15
    description = "Email verifier via mailcheck.p.rapidapi.com (disposable/role/SMTP)."

    def applicable(self, input: SearchInput) -> bool:
        return bool(input.email) and bool(get_settings().rapidapi_key)

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.email
        host = "mailcheck.p.rapidapi.com"
        async with _rapid_client(host) as c:
            try:
                r = await c.get(f"https://{host}/", params={"domain": input.email.split("@")[-1]})
            except httpx.HTTPError:
                return
        # AUDIT FIX: silent skip when not subscribed (403) or any non-200; don't
        # surface as collector error since the key is shared across endpoints.
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except ValueError:
            return
        yield Finding(
            collector=self.name,
            category="email",
            entity_type="EmailMailCheck",
            title=f"MailCheck {input.email}: valid={data.get('valid')}, disposable={data.get('disposable')}",
            url=None,
            confidence=0.85,
            payload=data,
        )


# --------------------------------------------------------------------------
# 4) Phone validator — "phonenumbervalidatefree.p.rapidapi.com"
# --------------------------------------------------------------------------
@register
class RapidAPIPhoneValidate(Collector):
    name = "rapidapi_phone_validate"
    category = "phone"
    needs = ("phone",)
    timeout_seconds = 15

    def applicable(self, input: SearchInput) -> bool:
        return bool(input.phone) and bool(get_settings().rapidapi_key)

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.phone
        host = "phonenumbervalidatefree.p.rapidapi.com"
        p = _digits_only(input.phone)
        async with _rapid_client(host) as c:
            try:
                r = await c.get(
                    f"https://{host}/ts_PhoneNumberValidateTest.jsp",
                    params={"Number": f"+{p}"},
                )
            except httpx.HTTPError:
                return
        # AUDIT FIX: silent skip on 403/non-200 — phone-validate sub is optional.
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:800]}
        yield Finding(
            collector=self.name,
            category="phone",
            entity_type="PhoneValidation",
            title=f"Validación teléfono {p}",
            url=None,
            confidence=0.7,
            payload=data,
        )


# --------------------------------------------------------------------------
# 5) Reverse image — "reverse-image-search1.p.rapidapi.com"
#    Solo se ejecuta si el usuario ha pegado URLs de imagen en extra_context.
# --------------------------------------------------------------------------
@register
class RapidAPIReverseImage(Collector):
    name = "rapidapi_reverse_image"
    category = "image"
    needs = ("extra_context",)
    timeout_seconds = 30

    def applicable(self, input: SearchInput) -> bool:
        if not get_settings().rapidapi_key:
            return False
        return bool(input.extra_context and ("http" in input.extra_context))

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        import re
        urls = re.findall(r'https?://\S+\.(?:jpg|jpeg|png|webp)', input.extra_context or "", re.I)
        if not urls:
            return
        host = "reverse-image-search1.p.rapidapi.com"
        async with _rapid_client(host) as c:
            for img in urls[:3]:
                try:
                    r = await c.get(f"https://{host}/reverse-image-search", params={"url": img, "limit": 20})
                except httpx.HTTPError:
                    continue
                # AUDIT FIX: silent skip on 403/non-200 — reverse-image sub optional.
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except ValueError:
                    continue
                for it in (data.get("matches") or data.get("results") or data if isinstance(data, list) else [])[:15]:
                    if not isinstance(it, dict):
                        continue
                    yield Finding(
                        collector=self.name,
                        category="image",
                        entity_type="ImageMatch",
                        title=f"Coincidencia inversa: {(it.get('title') or it.get('source') or img)[:150]}",
                        url=it.get("link") or it.get("url") or it.get("image"),
                        confidence=0.5,
                        payload={"source_image": img, **it},
                    )


# --------------------------------------------------------------------------
# 6) GuardDoxx — probe health + try common lookup paths
# --------------------------------------------------------------------------
@register
class RapidAPIGuardDoxx(Collector):
    name = "rapidapi_guarddoxx"
    category = "doxx"
    needs = ("email", "phone", "full_name", "birth_name", "aliases", "username")
    timeout_seconds = 30
    description = "GuardDoxx via RapidAPI (discovery probe; endpoints attempted dynamically)."

    def applicable(self, input: SearchInput) -> bool:
        if not get_settings().rapidapi_key:
            return False
        return any((input.email, input.phone, input.full_name, input.username))

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        host = "guarddoxx.p.rapidapi.com"
        base = f"https://{host}"
        async with _rapid_client(host) as c:
            # 1. Health check — confirms the subscription works.
            try:
                r = await c.get(f"{base}/health")
            except httpx.HTTPError:
                return
            # AUDIT FIX: don't raise on 403 — keep collector quiet if no sub.
            if r.status_code != 200:
                return
            try:
                health = r.json()
            except ValueError:
                health = {"text": r.text[:400]}

            yield Finding(
                collector=self.name,
                category="doxx",
                entity_type="GuardDoxxStatus",
                title=f"GuardDoxx alcanzable ({health.get('status') or 'ok'})",
                url=None,
                confidence=0.5,
                payload=health,
            )

            # 2. Try common discovery endpoints with each input (best-effort).
            tried: list[tuple[str, str, dict | None, str | None]] = []
            candidates: list[tuple[str, str, dict | None, str | None]] = []
            if input.email:
                candidates += [
                    ("POST", f"{base}/check", {"email": input.email}, None),
                    ("POST", f"{base}/lookup", {"email": input.email}, None),
                    ("POST", f"{base}/search", {"query": input.email}, None),
                    ("GET", f"{base}/email/{input.email}", None, None),
                ]
            if input.phone:
                p = _digits_only(input.phone)
                candidates += [
                    ("POST", f"{base}/check", {"phone": p}, None),
                    ("POST", f"{base}/phone", {"phone": p}, None),
                    ("GET", f"{base}/phone/{p}", None, None),
                ]
            if input.username:
                u = input.username.lstrip("@")
                candidates += [
                    ("POST", f"{base}/check", {"username": u}, None),
                    ("GET", f"{base}/username/{u}", None, None),
                ]
            if input.full_name:
                candidates += [
                    ("POST", f"{base}/search", {"name": input.full_name}, None),
                ]

            for method, url, body, _ in candidates[:10]:
                try:
                    if method == "GET":
                        rr = await c.get(url)
                    else:
                        rr = await c.post(url, json=body)
                except httpx.HTTPError:
                    continue
                if rr.status_code != 200:
                    tried.append((method, url, body, f"{rr.status_code}"))
                    continue
                try:
                    jd = rr.json()
                except ValueError:
                    jd = {"raw": rr.text[:600]}
                if not jd:
                    continue
                yield Finding(
                    collector=self.name,
                    category="doxx",
                    entity_type="GuardDoxxResult",
                    title=f"GuardDoxx {method} {url.rsplit('/',1)[-1]}",
                    url=url,
                    confidence=0.55,
                    payload={"request": body, "response": jd},
                )

            if tried:
                yield Finding(
                    collector=self.name,
                    category="doxx",
                    entity_type="GuardDoxxAttempts",
                    title="GuardDoxx: endpoints probados",
                    url=None,
                    confidence=0.2,
                    payload={"attempted": tried},
                )
