"""Verify candidate usernames/emails using cheap, independent signals.

Signals (each contributes weight to the score):
  - Gravatar lookup (200 -> strong positive on email)
  - DNS MX presence on the email's domain (necessary, not sufficient)
  - Optional `quick_check` hooks on registered collectors (for usernames)

The verifier never raises; failures are recorded and weighted at 0.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class VerifierResult:
    candidate: str
    kind: str  # "username" | "email"
    confirmations: list[str] = field(default_factory=list)
    score: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.score >= 0.5


# Signal weights — sum to ~1.0 when all positive.
W_GRAVATAR = 0.6
W_MX = 0.2
W_COLLECTOR = 0.5  # per collector quick_check hit (capped)


async def _gravatar_check(email: str, *, timeout: float = 5.0,
                          http_client: httpx.AsyncClient | None = None) -> tuple[bool, dict]:
    md5 = hashlib.md5(email.lower().strip().encode()).hexdigest()  # noqa: S324
    url = f"https://en.gravatar.com/{md5}.json"
    own = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        try:
            r = await client.get(url)
        except httpx.HTTPError as e:
            return False, {"error": str(e)}
        if r.status_code == 200:
            try:
                data = r.json()
            except Exception:
                data = {}
            return True, {"hash": md5, "profile": data}
        return False, {"hash": md5, "status": r.status_code}
    finally:
        if own:
            await client.aclose()


async def _mx_check(domain: str, *, timeout: float = 4.0) -> tuple[bool, dict]:
    """Cheap DNS check — returns True iff the domain has at least one MX record."""
    try:
        import dns.asyncresolver  # type: ignore
    except ImportError:
        return False, {"error": "dnspython not available"}
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout
    try:
        ans = await resolver.resolve(domain, "MX")
        hosts = [str(r.exchange).rstrip(".") for r in ans]
        return bool(hosts), {"mx": hosts}
    except Exception as e:
        return False, {"error": e.__class__.__name__}


async def _collector_quick_check(username: str, registry: Any) -> list[tuple[str, dict]]:
    """Run any registered collector that exposes a `quick_check(username)` coroutine.

    Returns list of (collector_name, payload) for hits.
    """
    if registry is None:
        return []
    hits: list[tuple[str, dict]] = []
    try:
        collectors = list(registry.all())
    except Exception:
        return []
    for c in collectors:
        fn = getattr(c, "quick_check", None)
        if not callable(fn):
            continue
        try:
            res = await fn(username)
        except Exception:
            continue
        if res:
            hits.append((getattr(c, "name", c.__class__.__name__),
                         res if isinstance(res, dict) else {"value": res}))
    return hits


async def verify_candidate(
    candidate: str,
    kind: str,
    *,
    collector_registry: Any = None,
    http_client: httpx.AsyncClient | None = None,
    enable_network: bool = True,
) -> VerifierResult:
    """Verify a single candidate. `kind` is 'username' or 'email'."""
    res = VerifierResult(candidate=candidate, kind=kind)
    if kind == "email":
        if "@" not in candidate:
            return res
        local, _, domain = candidate.partition("@")
        if enable_network:
            mx_ok, mx_info = await _mx_check(domain)
            res.payload["mx"] = mx_info
            if mx_ok:
                res.confirmations.append("mx")
                res.score += W_MX
            grav_ok, grav_info = await _gravatar_check(candidate, http_client=http_client)
            res.payload["gravatar"] = grav_info
            if grav_ok:
                res.confirmations.append("gravatar")
                res.score += W_GRAVATAR
    elif kind == "username":
        hits = await _collector_quick_check(candidate, collector_registry) if enable_network else []
        if hits:
            for name, payload in hits[:4]:
                res.confirmations.append(f"collector:{name}")
                res.payload.setdefault("collectors", {})[name] = payload
            res.score += min(len(hits) * W_COLLECTOR, 1.0)
    res.score = round(min(res.score, 1.0), 3)
    return res


async def verify_many(
    candidates: list[tuple[str, str]],
    *,
    concurrency: int = 8,
    **kwargs: Any,
) -> list[VerifierResult]:
    sem = asyncio.Semaphore(concurrency)

    async def _one(c: str, k: str) -> VerifierResult:
        async with sem:
            return await verify_candidate(c, k, **kwargs)

    return await asyncio.gather(*[_one(c, k) for c, k in candidates])
