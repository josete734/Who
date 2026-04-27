"""Tests for app.observability.metrics decorators and helpers."""
from __future__ import annotations

import asyncio

import pytest

from app.observability.metrics import (
    COLLECTOR_DURATION,
    COLLECTOR_RUNS,
    LLM_COST_USD,
    LLM_TOKENS,
    observe_collector,
    record_llm_call,
)


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def _hist_count(hist, **labels) -> float:
    return hist.labels(**labels)._sum.get(), sum(b.get() for b in hist.labels(**labels)._buckets)


async def test_observe_collector_success_increments_counter_and_duration():
    name = "unit_test_ok"
    before_runs = _counter_value(COLLECTOR_RUNS, collector=name, status="success")
    _, before_obs = _hist_count(COLLECTOR_DURATION, collector=name)

    @observe_collector(name)
    async def fn():
        await asyncio.sleep(0.01)
        return 42

    assert await fn() == 42

    after_runs = _counter_value(COLLECTOR_RUNS, collector=name, status="success")
    _, after_obs = _hist_count(COLLECTOR_DURATION, collector=name)
    assert after_runs == before_runs + 1
    assert after_obs >= before_obs + 1


async def test_observe_collector_error_status():
    name = "unit_test_err"
    before = _counter_value(COLLECTOR_RUNS, collector=name, status="error")

    @observe_collector(name)
    async def fn():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await fn()

    after = _counter_value(COLLECTOR_RUNS, collector=name, status="error")
    assert after == before + 1


async def test_observe_collector_timeout_status():
    name = "unit_test_to"
    before = _counter_value(COLLECTOR_RUNS, collector=name, status="timeout")

    @observe_collector(name)
    async def fn():
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        await fn()

    after = _counter_value(COLLECTOR_RUNS, collector=name, status="timeout")
    assert after == before + 1


def test_record_llm_call_tokens_and_cost():
    provider, model = "openai", "gpt-4o-mini"
    in_before = _counter_value(LLM_TOKENS, provider=provider, model=model, kind="input")
    out_before = _counter_value(LLM_TOKENS, provider=provider, model=model, kind="output")
    cost_before = _counter_value(LLM_COST_USD, provider=provider, model=model)

    cost = record_llm_call(provider, model, input_tokens=1000, output_tokens=500)

    # gpt-4o-mini: $0.00015/1k input, $0.0006/1k output
    assert cost == pytest.approx(0.00015 * 1 + 0.0006 * 0.5, rel=1e-6)
    assert _counter_value(LLM_TOKENS, provider=provider, model=model, kind="input") == in_before + 1000
    assert _counter_value(LLM_TOKENS, provider=provider, model=model, kind="output") == out_before + 500
    assert _counter_value(LLM_COST_USD, provider=provider, model=model) == pytest.approx(cost_before + cost)


def test_record_llm_call_ollama_is_free():
    cost = record_llm_call("ollama", "llama3.1", input_tokens=10_000, output_tokens=10_000)
    assert cost == 0.0
