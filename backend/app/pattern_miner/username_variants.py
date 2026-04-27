"""Generate candidate usernames from a person's names.

Produces 50–200 variants combining common patterns:
- firstlast, f.last, first_last, firstl, lfirst
- year suffixes, leetspeak, separators (., _, -)
- Spanish diminutive expansion (Pepe <-> José, etc.)
"""
from __future__ import annotations

import itertools
import re
from typing import Iterable

try:
    from unidecode import unidecode  # type: ignore
except ImportError:  # pragma: no cover - dependency declared but allow soft fallback
    def unidecode(s: str) -> str:
        # Minimal ASCII fold for tests if unidecode missing.
        repl = str.maketrans("áàäâãéèëêíìïîóòöôõúùüûñçÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇ",
                              "aaaaaeeeeiiiioooooouuuuncAAAAAEEEEIIIIOOOOOUUUUNC")
        return s.translate(repl)


# Spanish diminutives / hypocoristics. Bidirectional: each canonical name maps
# to nicknames; we also expand nicknames back to canonical forms.
ES_DIMINUTIVES: dict[str, list[str]] = {
    "jose": ["pepe", "pepito", "josito"],
    "francisco": ["paco", "pancho", "fran", "curro", "kiko"],
    "manuel": ["manolo", "manu", "lolo"],
    "ignacio": ["nacho"],
    "antonio": ["toni", "tono"],
    "alejandro": ["alex", "ale", "jandro"],
    "rafael": ["rafa"],
    "ricardo": ["ricky", "rica"],
    "roberto": ["rober", "berto"],
    "fernando": ["fer", "nando"],
    "guillermo": ["guille", "willy"],
    "sebastian": ["seba", "sebas"],
    "santiago": ["santi"],
    "javier": ["javi"],
    "enrique": ["quique", "kike"],
    "eduardo": ["edu", "lalo"],
    "daniel": ["dani"],
    "gabriel": ["gabi"],
    "miguel": ["mike", "migue"],
    "carlos": ["carlitos", "charly"],
    "cristina": ["cris", "cristi"],
    "isabel": ["isa", "chabela"],
    "maria": ["mari", "mary", "marita"],
    "rosa": ["rosi"],
    "concepcion": ["concha", "conchi"],
    "dolores": ["lola", "loli"],
    "mercedes": ["meche", "merche"],
    "monserrat": ["montse"],
    "pilar": ["pili"],
    "consuelo": ["chelo"],
    "rosario": ["charo"],
    "teresa": ["tere"],
    "guadalupe": ["lupe", "lupita"],
    "patricia": ["patri", "pati"],
    "beatriz": ["bea"],
    "alicia": ["ali"],
    "ana": ["anita"],
    "elena": ["nena"],
    "esperanza": ["espe"],
}

LEET_MAP = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5"}

YEAR_SUFFIXES = ["", "1", "01", "23", "24", "25", "1990", "1985", "1980", "2000", "07", "99", "_"]


_token_re = re.compile(r"[^a-z0-9]+")


def _ascii(s: str) -> str:
    return unidecode(s).lower().strip()


def _tokenize(name: str) -> list[str]:
    """Split a name into clean ascii tokens, dropping particles."""
    raw = _ascii(name)
    parts = [p for p in _token_re.split(raw) if p]
    # Drop common Spanish particles that are noise in usernames.
    stop = {"de", "del", "la", "las", "el", "los", "y", "san", "santa", "do", "da"}
    return [p for p in parts if p not in stop]


def _expand_diminutives(token: str) -> set[str]:
    """Return {token} plus diminutives/canonical variants."""
    out = {token}
    if token in ES_DIMINUTIVES:
        out.update(ES_DIMINUTIVES[token])
    # Reverse lookup: nickname -> canonical
    for canon, dims in ES_DIMINUTIVES.items():
        if token in dims:
            out.add(canon)
    return out


def _leet(s: str) -> str:
    return "".join(LEET_MAP.get(c, c) for c in s)


def _split_first_last(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Return (firsts, lasts). For Spanish names usually 1-2 firsts, 1-2 lasts."""
    if not tokens:
        return [], []
    if len(tokens) == 1:
        return [tokens[0]], []
    if len(tokens) == 2:
        return [tokens[0]], [tokens[1]]
    if len(tokens) == 3:
        return [tokens[0]], [tokens[1], tokens[2]]
    # 4+: assume first 2 are given names, rest are surnames.
    return tokens[:2], tokens[2:]


def _gather_names(full_name: str | None,
                  birth_name: str | None,
                  aliases: Iterable[str] | str | None) -> list[list[str]]:
    """Return list of token-lists, one per name source."""
    sources: list[str] = []
    for n in (full_name, birth_name):
        if n:
            sources.append(n)
    if aliases:
        if isinstance(aliases, str):
            for a in aliases.split(","):
                a = a.strip()
                if a:
                    sources.append(a)
        else:
            for a in aliases:
                if a:
                    sources.append(a)
    seen: set[tuple[str, ...]] = set()
    out: list[list[str]] = []
    for src in sources:
        toks = _tokenize(src)
        if not toks:
            continue
        key = tuple(toks)
        if key in seen:
            continue
        seen.add(key)
        out.append(toks)
    return out


def generate_username_variants(
    full_name: str | None = None,
    birth_name: str | None = None,
    aliases: Iterable[str] | str | None = None,
    *,
    max_variants: int = 200,
    include_leet: bool = True,
    include_years: bool = True,
) -> list[str]:
    """Deterministic generation of candidate usernames.

    Returns a sorted, deduplicated list capped at `max_variants`.
    """
    candidates: set[str] = set()
    name_sets = _gather_names(full_name, birth_name, aliases)
    if not name_sets:
        return []

    seps = ["", ".", "_", "-"]

    for tokens in name_sets:
        firsts, lasts = _split_first_last(tokens)
        if not firsts:
            continue

        # Expand diminutives on the first given name only.
        first_variants = sorted(_expand_diminutives(firsts[0]))
        # Other given names kept as-is.
        rest_firsts = firsts[1:]
        # Surnames: try each, plus combined.
        last_variants: list[str] = []
        if lasts:
            last_variants.extend(lasts)
            if len(lasts) > 1:
                last_variants.append("".join(lasts))

        for first in first_variants:
            # Bare first name
            candidates.add(first)
            for sep in seps:
                if rest_firsts:
                    candidates.add(sep.join([first, *rest_firsts]))

            for last in last_variants or [""]:
                if not last:
                    continue
                fi = first[0]
                li = last[0]
                # Core patterns
                base = [
                    f"{first}{last}",
                    f"{last}{first}",
                    f"{fi}{last}",
                    f"{first}{li}",
                    f"{last}{fi}",
                    f"{li}{first}",
                ]
                for sep in seps:
                    base.extend([
                        f"{first}{sep}{last}",
                        f"{last}{sep}{first}",
                        f"{fi}{sep}{last}",
                        f"{first}{sep}{li}",
                    ])
                # With middle/second given name
                for mid in rest_firsts:
                    base.append(f"{first}.{mid}.{last}")
                    base.append(f"{first}{mid[0]}{last}")
                for v in base:
                    if 2 <= len(v) <= 40:
                        candidates.add(v)

    # Augment with year suffixes / leet on a snapshot to avoid combinatorial blow-up.
    snapshot = sorted(candidates)
    if include_years:
        for v in snapshot:
            for y in YEAR_SUFFIXES:
                if y:
                    candidates.add(f"{v}{y}")
    if include_leet:
        for v in snapshot[:40]:  # cap leet expansion
            leeted = _leet(v)
            if leeted != v:
                candidates.add(leeted)

    # Final sort/dedup. Sort by (len, value) for determinism and to prefer shorter/cleaner ones first.
    out = sorted(candidates, key=lambda s: (len(s), s))
    return out[:max_variants]
