"""Generate candidate emails for one or more domains.

Optional: detect company domains automatically from previously-collected
BORME findings (stored as Finding rows with collector='borme').
"""
from __future__ import annotations

import re
from typing import Iterable, Sequence

from app.pattern_miner.username_variants import (
    _ascii,
    _gather_names,
    _split_first_last,
    _expand_diminutives,
)


# Common patterns used by enterprise email systems.
# Each is a callable taking (first, mid_initials, last_tokens) and returning local-part.
def _patterns(first: str, rest_firsts: list[str], lasts: list[str]) -> list[str]:
    out: list[str] = []
    if not first:
        return out
    fi = first[0]
    last = lasts[0] if lasts else ""
    last_full = "".join(lasts) if lasts else ""
    li = last[0] if last else ""
    if last:
        out.extend([
            f"{first}.{last}",
            f"{first}_{last}",
            f"{first}-{last}",
            f"{first}{last}",
            f"{fi}.{last}",
            f"{fi}{last}",
            f"{first}.{li}",
            f"{first}{li}",
            f"{last}.{first}",
            f"{last}{first}",
            f"{li}{first}",
            f"{li}.{first}",
        ])
    else:
        out.append(first)
    if last_full and last_full != last:
        out.extend([
            f"{first}.{last_full}",
            f"{fi}.{last_full}",
            f"{first}{last_full}",
        ])
    for mid in rest_firsts:
        if last:
            mi = mid[0]
            out.extend([
                f"{first}.{mid}.{last}",
                f"{first}{mi}.{last}",
                f"{first}{mi}{last}",
            ])
    return out


_DOMAIN_RE = re.compile(r"\b([a-z0-9-]+(?:\.[a-z0-9-]+)+)\b", re.I)


def extract_domains_from_borme(payloads: Iterable[dict]) -> list[str]:
    """Best-effort: scrape probable corporate domains from BORME-style payloads.

    Looks at common keys ('domain', 'website', 'url') and any string fields,
    filtering out registry domains.
    """
    blacklist = {"boe.es", "borme.es", "registradores.org", "google.com", "linkedin.com"}
    found: list[str] = []
    seen: set[str] = set()
    for p in payloads or []:
        if not isinstance(p, dict):
            continue
        for key in ("domain", "website", "site", "url", "homepage"):
            v = p.get(key)
            if isinstance(v, str):
                for m in _DOMAIN_RE.finditer(v):
                    d = m.group(1).lower().lstrip("www.")
                    if d in blacklist or d in seen:
                        continue
                    if "." not in d or len(d) < 4:
                        continue
                    seen.add(d)
                    found.append(d)
    return found


def generate_email_variants(
    domain: str | Sequence[str],
    full_name: str | None = None,
    birth_name: str | None = None,
    aliases: Iterable[str] | str | None = None,
    *,
    max_variants: int = 200,
) -> list[str]:
    """Generate candidate emails for one or many domains."""
    domains: list[str] = [domain] if isinstance(domain, str) else [d for d in domain if d]
    domains = [d.lower().strip().lstrip("@") for d in domains if d]
    if not domains:
        return []

    name_sets = _gather_names(full_name, birth_name, aliases)
    locals_: set[str] = set()
    for tokens in name_sets:
        firsts, lasts = _split_first_last(tokens)
        if not firsts:
            continue
        for first in sorted(_expand_diminutives(firsts[0])):
            for lp in _patterns(first, firsts[1:], lasts):
                lp = _ascii(lp)
                if 1 <= len(lp) <= 64 and re.fullmatch(r"[a-z0-9._\-]+", lp):
                    locals_.add(lp)

    out: list[str] = []
    for lp in sorted(locals_, key=lambda s: (len(s), s)):
        for d in domains:
            out.append(f"{lp}@{d}")
            if len(out) >= max_variants:
                return out
    return out
