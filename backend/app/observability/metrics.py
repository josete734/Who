"""Prometheus metrics + observability helpers.

All metrics are module-level singletons attached to the default global
registry. Importing this module multiple times is safe; collisions during
test runs (which re-import) are handled by reusing existing collectors
when present.
"""
from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from typing import Any, Awaitable, Callable, Iterator

from prometheus_client import REGISTRY, Counter, Gauge, Histogram


def _get_or_create(cls, name: str, documentation: str, labelnames: tuple[str, ...] = (), **kwargs):
    """Reuse an existing collector if a previous import registered it."""
    existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
    if existing is not None:
        return existing
    if labelnames:
        return cls(name, documentation, labelnames=list(labelnames), **kwargs)
    return cls(name, documentation, **kwargs)


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

COLLECTOR_RUNS = _get_or_create(
    Counter,
    "collector_runs_total",
    "Number of collector runs by status (success/timeout/error/skipped).",
    ("collector", "status"),
)

COLLECTOR_DURATION = _get_or_create(
    Histogram,
    "collector_duration_seconds",
    "Wall-clock duration of collector runs in seconds.",
    ("collector",),
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)

COLLECTOR_FINDINGS = _get_or_create(
    Counter,
    "collector_findings_total",
    "Findings produced by a collector, labelled by finding kind.",
    ("collector", "kind"),
)

CACHE_EVENTS = _get_or_create(
    Counter,
    "cache_events_total",
    "Cache events (hit/miss/set).",
    ("kind",),
)

LLM_TOKENS = _get_or_create(
    Counter,
    "llm_tokens_total",
    "LLM tokens consumed by provider/model and direction (input/output).",
    ("provider", "model", "kind"),
)

LLM_COST_USD = _get_or_create(
    Counter,
    "llm_cost_usd_total",
    "Cumulative LLM cost in USD by provider/model.",
    ("provider", "model"),
)

CASE_DURATION = _get_or_create(
    Histogram,
    "case_duration_seconds",
    "End-to-end case run duration in seconds.",
    (),
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)

CASE_ACTIVE = _get_or_create(
    Gauge,
    "case_active",
    "Number of currently in-flight cases.",
)

# Per-collector cache hit rate (0..1). Updated periodically from Redis stats
# via ``refresh_cache_hit_rate``. Exposed so a Prometheus query like
# ``cache_hit_rate{collector="crtsh"}`` is directly available without a
# bespoke API endpoint.
CACHE_HIT_RATE = _get_or_create(
    Gauge,
    "cache_hit_rate",
    "Rolling cache hit rate per collector in [0,1] (refreshed periodically).",
    ("collector",),
)

# Histogram for orchestrator phases. Phases include: collection,
# entity_resolution, triangulation, synthesis.
CASE_COLLECTOR_PHASE_SECONDS = _get_or_create(
    Histogram,
    "case_collector_phase_seconds",
    "Duration of orchestrator phases per case (collection/entity_resolution/triangulation/synthesis).",
    ("phase",),
    buckets=(0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
)


# ---------------------------------------------------------------------------
# LLM cost table (USD per 1K tokens)
# Sources: vendor public pricing pages snapshots; Ollama is local => 0.
# ---------------------------------------------------------------------------

LLM_PRICING_PER_1K: dict[tuple[str, str], tuple[float, float]] = {
    # (provider, model) -> (input_per_1k, output_per_1k)
    # Google Gemini
    ("gemini", "gemini-1.5-flash"): (0.000075, 0.0003),
    ("gemini", "gemini-1.5-pro"): (0.00125, 0.005),
    ("gemini", "gemini-2.0-flash"): (0.0001, 0.0004),
    ("gemini", "gemini-2.5-pro"): (0.00125, 0.010),
    # OpenAI
    ("openai", "gpt-4o"): (0.0025, 0.010),
    ("openai", "gpt-4o-mini"): (0.00015, 0.0006),
    ("openai", "gpt-4.1"): (0.002, 0.008),
    ("openai", "gpt-4.1-mini"): (0.0004, 0.0016),
    # Anthropic Claude
    ("claude", "claude-3-5-sonnet"): (0.003, 0.015),
    ("claude", "claude-3-5-haiku"): (0.0008, 0.004),
    ("claude", "claude-opus-4"): (0.015, 0.075),
    ("claude", "claude-sonnet-4"): (0.003, 0.015),
    # Ollama (local) — always free
    ("ollama", "*"): (0.0, 0.0),
}


def _lookup_price(provider: str, model: str) -> tuple[float, float]:
    p = provider.lower()
    if p == "ollama":
        return (0.0, 0.0)
    key = (p, model)
    if key in LLM_PRICING_PER_1K:
        return LLM_PRICING_PER_1K[key]
    # try prefix match (e.g. "gpt-4o-2024-08-06" -> "gpt-4o")
    for (prov, m), price in LLM_PRICING_PER_1K.items():
        if prov == p and model.startswith(m):
            return price
    return (0.0, 0.0)


def record_llm_call(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Record token usage and incremental USD cost. Returns cost in USD."""
    LLM_TOKENS.labels(provider=provider, model=model, kind="input").inc(input_tokens)
    LLM_TOKENS.labels(provider=provider, model=model, kind="output").inc(output_tokens)
    in_price, out_price = _lookup_price(provider, model)
    cost = (input_tokens / 1000.0) * in_price + (output_tokens / 1000.0) * out_price
    if cost:
        LLM_COST_USD.labels(provider=provider, model=model).inc(cost)
    return cost


# ---------------------------------------------------------------------------
# Decorators / context managers
# ---------------------------------------------------------------------------


def observe_collector(name: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Async decorator: time a collector call, record status + duration.

    Status values:
      - success: returned normally
      - timeout: TimeoutError / asyncio.TimeoutError
      - error:   any other exception (re-raised)
      - skipped: caller raised ``CollectorSkipped`` (duck-typed by class name)
    """
    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            status = "success"
            try:
                return await fn(*args, **kwargs)
            except TimeoutError:
                status = "timeout"
                raise
            except Exception as exc:
                if exc.__class__.__name__ == "CollectorSkipped":
                    status = "skipped"
                else:
                    status = "error"
                raise
            finally:
                elapsed = time.perf_counter() - start
                COLLECTOR_DURATION.labels(collector=name).observe(elapsed)
                COLLECTOR_RUNS.labels(collector=name, status=status).inc()
        return wrapper
    return decorator


@contextmanager
def track_case() -> Iterator[None]:
    """Context manager to mark a case in flight and record its total duration."""
    CASE_ACTIVE.inc()
    start = time.perf_counter()
    try:
        yield
    finally:
        CASE_DURATION.observe(time.perf_counter() - start)
        CASE_ACTIVE.dec()


def record_finding(collector: str, kind: str, n: int = 1) -> None:
    COLLECTOR_FINDINGS.labels(collector=collector, kind=kind).inc(n)


def record_cache_event(kind: str, collector: str | None = None) -> None:
    """kind in {'hit','miss','set'}. Also bumps the per-collector counter
    by reusing ``COLLECTOR_RUNS``-style labels via ``CACHE_EVENTS`` (kind only).
    Per-collector hit rate is computed from Redis stats by
    :func:`refresh_cache_hit_rate`.
    """
    CACHE_EVENTS.labels(kind=kind).inc()


async def refresh_cache_hit_rate() -> dict[str, float]:
    """Recompute the per-collector cache hit rate Gauge from Redis stats.

    Reads ``cache:stats:hit:<collector>`` / ``cache:stats:miss:<collector>``
    counters via :func:`app.cache.get_stats`, computes ``hit / (hit+miss)``
    per collector, and writes the result into ``CACHE_HIT_RATE``. Returns
    the mapping it computed.

    Intended to be called from a periodic background task (e.g. arq cron)
    so a Prometheus query like ``cache_hit_rate{collector="crtsh"}`` stays
    fresh without adding an API endpoint.
    """
    try:
        from app.cache import get_stats
        stats = await get_stats()
    except Exception:  # pragma: no cover - defensive
        return {}

    hits: dict[str, int] = {}
    misses: dict[str, int] = {}
    for k, v in stats.items():
        # keys look like "hit", "miss", "hit:<collector>", "miss:<collector>"
        if k.startswith("hit:"):
            hits[k[4:]] = int(v)
        elif k.startswith("miss:"):
            misses[k[5:]] = int(v)

    out: dict[str, float] = {}
    for collector in set(hits) | set(misses):
        h = hits.get(collector, 0)
        m = misses.get(collector, 0)
        total = h + m
        rate = (h / total) if total > 0 else 0.0
        out[collector] = rate
        try:
            CACHE_HIT_RATE.labels(collector=collector).set(rate)
        except Exception:  # pragma: no cover
            pass
    return out


# WIRING (base collector class, e.g. backend/app/collectors/base.py):
#   from app.observability.metrics import observe_collector, record_finding
#
#   class BaseCollector:
#       name: str = "base"
#
#       async def run(self, *args, **kwargs):
#           wrapped = observe_collector(self.name)(self._run)
#           findings = await wrapped(*args, **kwargs)
#           for f in findings or []:
#               record_finding(self.name, getattr(f, "kind", "unknown"))
#           return findings
#
# WIRING (cache layer, e.g. backend/app/cache.py):
#   from app.observability.metrics import record_cache_event
#   record_cache_event("hit"|"miss"|"set")
#
# WIRING (LLM clients, e.g. backend/app/llm/*):
#   from app.observability.metrics import record_llm_call
#   record_llm_call("gemini", "gemini-1.5-flash", in_tokens, out_tokens)
#
# WIRING (orchestrator.py case run):
#   from app.observability.metrics import track_case
#   with track_case():
#       await run_case(...)
