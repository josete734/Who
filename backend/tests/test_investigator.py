"""Tests for the autonomous AI investigator (Wave 2/B2).

Uses a fake LLM client + fake dispatcher so the loop, max-steps cap and
finalize semantics can be exercised offline.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.ai_investigator.report import InvestigatorReport
from app.ai_investigator.runner import (
    InvestigatorRunner,
    LLMTurn,
    StepEvent,
    ToolCall,
)
from app.ai_investigator.tools import (
    TOOL_ADD_PIVOT,
    TOOL_FINALIZE_REPORT,
    TOOL_GET_FINDINGS,
    TOOL_RUN_COLLECTOR,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.findings: list[dict[str, Any]] = [
            {"collector": "sherlock", "title": "github.com/alice42", "category": "username"}
        ]
        self.entities = {"people": [], "accounts": [{"value": "alice42"}]}
        self.pivots: list[tuple[str, str]] = []

    async def run_collector(self, case_id, name, inputs):
        self.calls.append((name, inputs))
        return {"collector": name, "findings_added": 1}

    async def get_findings(self, case_id, filter=None):
        return self.findings

    async def get_entities(self, case_id):
        return self.entities

    async def add_pivot(self, case_id, kind, value):
        self.pivots.append((kind, value))
        return {"ok": True, "kind": kind, "value": value}


class ScriptedLLM:
    """LLM client that returns pre-baked turns from a queue."""

    def __init__(self, script: list[LLMTurn]) -> None:
        self.script = list(script)
        self.invocations: list[dict[str, Any]] = []

    async def generate_with_tools(self, system, messages, tools):
        self.invocations.append(
            {"system_len": len(system), "messages": list(messages), "tools": tools}
        )
        if not self.script:
            return LLMTurn(text="(scripted exhaustion)")
        return self.script.pop(0)


def _tc(name: str, args: dict[str, Any], tid: str = "t1") -> ToolCall:
    return ToolCall(id=tid, name=name, arguments=args)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def _collect(runner: InvestigatorRunner) -> list[StepEvent]:
    out: list[StepEvent] = []
    async for ev in runner.run():
        out.append(ev)
    return out


async def test_loop_executes_tools_and_finalizes() -> None:
    case_id = uuid.uuid4()
    dispatcher = FakeDispatcher()

    script = [
        LLMTurn(
            tool_calls=[
                _tc(TOOL_RUN_COLLECTOR, {"name": "sherlock", "inputs": {"username": "alice42"}}, "c1")
            ]
        ),
        LLMTurn(tool_calls=[_tc(TOOL_GET_FINDINGS, {"filter": {"collector": "sherlock"}}, "c2")]),
        LLMTurn(tool_calls=[_tc(TOOL_ADD_PIVOT, {"kind": "username", "value": "alice42"}, "c3")]),
        LLMTurn(
            tool_calls=[
                _tc(
                    TOOL_FINALIZE_REPORT,
                    {
                        "summary": "Found alice42 on GitHub.",
                        "confidence": 0.75,
                        "gaps": ["No email"],
                        "key_entities": ["alice42"],
                    },
                    "c4",
                )
            ]
        ),
    ]
    llm = ScriptedLLM(script)
    runner = InvestigatorRunner(
        case_id=case_id, dispatcher=dispatcher, llm=llm, max_steps=8
    )
    events = await _collect(runner)

    kinds = [e.kind for e in events]
    assert kinds.count("tool_call") == 4
    assert kinds.count("tool_result") == 4
    assert kinds[-1] == "final"
    assert events[-1].name == TOOL_FINALIZE_REPORT
    assert dispatcher.calls == [("sherlock", {"username": "alice42"})]
    assert dispatcher.pivots == [("username", "alice42")]
    assert isinstance(runner.report, InvestigatorReport)
    assert runner.report.confidence_overall == pytest.approx(0.75)
    assert "alice42" in runner.report.key_entities


async def test_max_steps_cap() -> None:
    """Runner must stop at max_steps even if model never finalizes."""
    case_id = uuid.uuid4()
    dispatcher = FakeDispatcher()
    forever = [
        LLMTurn(tool_calls=[_tc(TOOL_GET_FINDINGS, {"filter": {}}, f"c{i}")])
        for i in range(20)
    ]
    llm = ScriptedLLM(forever)

    runner = InvestigatorRunner(
        case_id=case_id, dispatcher=dispatcher, llm=llm, max_steps=3
    )
    events = await _collect(runner)

    assert events[-1].kind == "max_steps"
    # Exactly max_steps tool_call events (one per step in this script).
    assert sum(1 for e in events if e.kind == "tool_call") == 3
    assert runner.report is None


async def test_finalize_report_terminates_immediately() -> None:
    """If finalize is called on step 1, runner stops without further LLM turns."""
    case_id = uuid.uuid4()
    dispatcher = FakeDispatcher()
    llm = ScriptedLLM(
        [
            LLMTurn(
                tool_calls=[
                    _tc(
                        TOOL_FINALIZE_REPORT,
                        {"summary": "nothing to do", "confidence": 0.1},
                        "c1",
                    )
                ]
            ),
            LLMTurn(text="should-not-run"),
        ]
    )
    runner = InvestigatorRunner(
        case_id=case_id, dispatcher=dispatcher, llm=llm, max_steps=8
    )
    events = await _collect(runner)

    assert any(e.kind == "final" for e in events)
    # Only one LLM invocation happened.
    assert len(llm.invocations) == 1
    assert runner.report is not None
    assert runner.report.summary == "nothing to do"


async def test_unknown_tool_returns_error_result() -> None:
    case_id = uuid.uuid4()
    dispatcher = FakeDispatcher()
    llm = ScriptedLLM(
        [
            LLMTurn(tool_calls=[_tc("not_a_tool", {}, "c1")]),
            LLMTurn(
                tool_calls=[
                    _tc(
                        TOOL_FINALIZE_REPORT,
                        {"summary": "done", "confidence": 0.5},
                        "c2",
                    )
                ]
            ),
        ]
    )
    runner = InvestigatorRunner(
        case_id=case_id, dispatcher=dispatcher, llm=llm, max_steps=4
    )
    events = await _collect(runner)
    tool_results = [e for e in events if e.kind == "tool_result"]
    assert tool_results[0].data.get("error", "").startswith("unknown tool")
    assert runner.report is not None


async def test_text_only_turn_is_terminal() -> None:
    """If the model emits a plain text turn (no tool calls), the loop stops."""
    case_id = uuid.uuid4()
    dispatcher = FakeDispatcher()
    llm = ScriptedLLM([LLMTurn(text="I cannot proceed under GDPR.")])
    runner = InvestigatorRunner(
        case_id=case_id, dispatcher=dispatcher, llm=llm, max_steps=5
    )
    events = await _collect(runner)
    assert events[-1].kind == "final"
    assert "GDPR" in events[-1].data["text"]
    assert runner.report is None


def test_step_event_to_sse_format() -> None:
    ev = StepEvent(step=2, kind="tool_call", name="run_collector", data={"x": 1})
    sse = ev.to_sse()
    assert sse.startswith("event: tool_call\n")
    assert "\"step\": 2" in sse
    assert sse.endswith("\n\n")
