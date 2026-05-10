"""Tests for the Plan / Verifier scaffolding (roadmap #7 stages 1-3).

Stage 1 shipped data structures + Protocol; stage 2 wired NullVerifier
into the loop; stage 3a added LLMVerifier (asks an LLM to grade
progress against `success_criteria`); stage 3b adds LLMPlanner
(asks an LLM to decompose a task into PlanSteps).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openmimi.planning import (
    LLMPlanner,
    LLMVerifier,
    NullVerifier,
    Plan,
    PlanStep,
    Verifier,
    _parse_outcome,
    _parse_plan_steps,
    _single_step_plan,
    _summarize_messages,
)


def test_planstep_defaults() -> None:
    step = PlanStep(step="open homepage", success_criteria="page loaded")
    assert step.allowed_tools is None
    assert step.budget is None
    assert step.done is False


def test_planstep_roundtrip() -> None:
    original = PlanStep(
        step="search 'laptop'",
        success_criteria=">=1 result visible",
        allowed_tools=["agent_browser"],
        budget=5,
        done=True,
    )
    restored = PlanStep.from_dict(original.to_dict())
    assert restored == original


def test_plan_current_and_advance() -> None:
    plan = Plan(
        steps=[
            PlanStep(step="a", success_criteria="ok"),
            PlanStep(step="b", success_criteria="ok"),
        ]
    )
    first = plan.current()
    assert first is not None and first.step == "a"
    plan.advance()
    assert first.done is True
    second = plan.current()
    assert second is not None and second.step == "b"


def test_plan_is_complete_after_all_steps_advanced() -> None:
    plan = Plan(steps=[PlanStep(step="a", success_criteria="ok")])
    assert plan.is_complete() is False
    plan.advance()
    assert plan.is_complete() is True
    assert plan.current() is None


def test_plan_advance_past_end_is_safe() -> None:
    plan = Plan(steps=[PlanStep(step="a", success_criteria="ok")])
    plan.advance()
    plan.advance()  # second advance should not raise
    assert plan.is_complete() is True


def test_empty_plan_is_complete() -> None:
    plan = Plan()
    assert plan.is_complete() is True
    assert plan.current() is None


def test_plan_roundtrip() -> None:
    plan = Plan(
        steps=[
            PlanStep(step="a", success_criteria="ok", budget=3),
            PlanStep(step="b", success_criteria="ok", done=True),
        ],
        current_step=1,
    )
    restored = Plan.from_dict(plan.to_dict())
    assert restored == plan


def test_null_verifier_returns_continue() -> None:
    verifier = NullVerifier()
    plan = Plan(steps=[PlanStep(step="a", success_criteria="ok")])
    outcome = asyncio.run(verifier.verify(plan, []))
    assert outcome == "continue"


def test_null_verifier_satisfies_protocol() -> None:
    # Protocol is runtime_checkable, so isinstance should work.
    assert isinstance(NullVerifier(), Verifier)


@pytest.mark.parametrize(
    "messages",
    [[], [{"role": "user", "content": "hello"}]],
)
def test_null_verifier_ignores_messages(messages: list[dict]) -> None:
    verifier = NullVerifier()
    plan = Plan()
    outcome = asyncio.run(verifier.verify(plan, messages))
    assert outcome == "continue"


def test_enable_planning_defaults_to_false() -> None:
    """Stage 1 ships data structures only; planning stays off by default."""
    from openmimi.config.schema import AppConfig

    assert AppConfig().enable_planning is False


# --- LLMVerifier (stage 3) --------------------------------------------------


class _ScriptedLLM:
    """Minimal LLMClient stub returning preset responses in order."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
            }
        )
        if not self._responses:
            raise AssertionError("ScriptedLLM ran out of responses")
        return self._responses.pop(0)


def _text_response(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"outcome": "done", "reason": "x"}', "done"),
        ('{"outcome":"continue","reason":"y"}', "continue"),
        ('{"outcome": "replan", "reason": "z"}', "replan"),
        ('Here is the JSON: {"outcome": "done", "reason": "x"}', "done"),
        ("```json\n{\"outcome\": \"done\"}\n```", "done"),
    ],
)
def test_parse_outcome_extracts_valid_outcomes(raw: str, expected: str) -> None:
    assert _parse_outcome(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no json here at all",
        '{"outcome": "maybe"}',  # invalid value
        '{"reason": "x"}',  # missing key
        "not json {{",
        '"done"',  # bare string is not the dict shape
    ],
)
def test_parse_outcome_returns_none_for_invalid(raw: str) -> None:
    assert _parse_outcome(raw) is None


def test_summarize_messages_renders_text_and_tool_blocks() -> None:
    summary = _summarize_messages(
        [
            {"role": "user", "content": "the original task"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "thinking"},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "browser",
                        "input": {"action": "click"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "clicked"}],
                    }
                ],
            },
        ]
    )
    assert "the original task" in summary
    assert "thinking" in summary
    assert "browser" in summary
    assert "clicked" in summary


def test_summarize_messages_marks_error_tool_results() -> None:
    summary = _summarize_messages(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "is_error": True,
                        "content": [{"type": "text", "text": "boom"}],
                    }
                ],
            }
        ]
    )
    assert "ERROR" in summary
    assert "boom" in summary


def test_llm_verifier_returns_done_when_no_current_step() -> None:
    """Empty/complete plan → done without consulting the LLM."""
    llm = _ScriptedLLM([])  # no responses needed
    verifier = LLMVerifier(llm)
    outcome = asyncio.run(verifier.verify(Plan(steps=[]), []))
    assert outcome == "done"
    assert llm.calls == []


def test_llm_verifier_passes_done_through() -> None:
    llm = _ScriptedLLM([_text_response('{"outcome": "done", "reason": "ok"}')])
    verifier = LLMVerifier(llm)
    plan = Plan(
        steps=[PlanStep(step="open page", success_criteria="page loaded")]
    )
    outcome = asyncio.run(verifier.verify(plan, []))
    assert outcome == "done"
    assert len(llm.calls) == 1
    assert "page loaded" in llm.calls[0]["messages"][0]["content"]


def test_llm_verifier_passes_continue_and_replan() -> None:
    llm = _ScriptedLLM(
        [
            _text_response('{"outcome": "continue", "reason": "still typing"}'),
            _text_response('{"outcome": "replan", "reason": "wrong tab"}'),
        ]
    )
    verifier = LLMVerifier(llm)
    plan = Plan(steps=[PlanStep(step="x", success_criteria="y")])
    a = asyncio.run(verifier.verify(plan, []))
    b = asyncio.run(verifier.verify(plan, []))
    assert (a, b) == ("continue", "replan")


def test_llm_verifier_falls_back_to_continue_on_malformed_reply() -> None:
    llm = _ScriptedLLM([_text_response("LLM had a bad day. {not_json")])
    verifier = LLMVerifier(llm)
    plan = Plan(steps=[PlanStep(step="x", success_criteria="y")])
    outcome = asyncio.run(verifier.verify(plan, []))
    assert outcome == "continue"


def test_llm_verifier_falls_back_to_continue_on_exception() -> None:
    class _BoomLLM:
        async def create(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("upstream is down")

    verifier = LLMVerifier(_BoomLLM())  # type: ignore[arg-type]
    plan = Plan(steps=[PlanStep(step="x", success_criteria="y")])
    outcome = asyncio.run(verifier.verify(plan, []))
    assert outcome == "continue"


def test_llm_verifier_clips_recent_messages_to_max_tail() -> None:
    """The verifier should only forward the last N messages to the LLM."""
    llm = _ScriptedLLM([_text_response('{"outcome": "continue"}')])
    verifier = LLMVerifier(llm, max_tail_messages=2)
    plan = Plan(steps=[PlanStep(step="x", success_criteria="grade me")])
    fake = [
        {"role": "user", "content": "old-1"},
        {"role": "user", "content": "old-2"},
        {"role": "user", "content": "old-3"},
        {"role": "user", "content": "recent-1"},
        {"role": "user", "content": "recent-2"},
    ]
    asyncio.run(verifier.verify(plan, fake))
    user_prompt = llm.calls[0]["messages"][0]["content"]
    assert "recent-1" in user_prompt
    assert "recent-2" in user_prompt
    assert "old-1" not in user_prompt
    assert "old-2" not in user_prompt


def test_llm_verifier_satisfies_protocol() -> None:
    """LLMVerifier must conform to the Verifier Protocol at runtime."""
    llm = _ScriptedLLM([])
    assert isinstance(LLMVerifier(llm), Verifier)


# --- LLMPlanner (stage 3b) --------------------------------------------------


def _plan_array_response(steps: list[dict[str, Any]]) -> dict[str, Any]:
    import json as _json
    return {"content": [{"type": "text", "text": _json.dumps(steps)}]}


def test_parse_plan_steps_extracts_minimal_array() -> None:
    raw = '[{"step": "open page", "success_criteria": "page loaded"}]'
    steps = _parse_plan_steps(raw)
    assert steps is not None
    assert len(steps) == 1
    assert steps[0].step == "open page"
    assert steps[0].success_criteria == "page loaded"
    assert steps[0].allowed_tools is None
    assert steps[0].budget is None


def test_parse_plan_steps_honors_optional_fields() -> None:
    raw = (
        '[{"step": "search", "success_criteria": ">=1 result", '
        '"allowed_tools": ["agent_browser"], "budget": 5}]'
    )
    steps = _parse_plan_steps(raw)
    assert steps is not None
    assert steps[0].allowed_tools == ["agent_browser"]
    assert steps[0].budget == 5


def test_parse_plan_steps_strips_whitespace() -> None:
    raw = '[{"step": "  click  ", "success_criteria": "  done  "}]'
    steps = _parse_plan_steps(raw)
    assert steps is not None
    assert steps[0].step == "click"
    assert steps[0].success_criteria == "done"


def test_parse_plan_steps_extracts_from_noisy_text() -> None:
    raw = (
        'Sure, here is the plan:\n'
        '[{"step": "a", "success_criteria": "ok"},'
        ' {"step": "b", "success_criteria": "ok"}]\n'
        'Hope that helps!'
    )
    steps = _parse_plan_steps(raw)
    assert steps is not None
    assert [s.step for s in steps] == ["a", "b"]


def test_parse_plan_steps_skips_malformed_entries() -> None:
    raw = (
        '[{"step": "good", "success_criteria": "ok"},'
        ' {"step": "missing criteria"},'
        ' {"success_criteria": "missing step"},'
        ' "not a dict",'
        ' {"step": "", "success_criteria": "empty step"},'
        ' {"step": "also good", "success_criteria": "ok"}]'
    )
    steps = _parse_plan_steps(raw)
    assert steps is not None
    assert [s.step for s in steps] == ["good", "also good"]


def test_parse_plan_steps_drops_non_string_tools() -> None:
    raw = (
        '[{"step": "a", "success_criteria": "ok", '
        '"allowed_tools": ["browser", 42, "", null]}]'
    )
    steps = _parse_plan_steps(raw)
    assert steps is not None
    assert steps[0].allowed_tools == ["browser"]


def test_parse_plan_steps_drops_invalid_budget() -> None:
    raw = (
        '[{"step": "a", "success_criteria": "ok", "budget": -1},'
        ' {"step": "b", "success_criteria": "ok", "budget": 0},'
        ' {"step": "c", "success_criteria": "ok", "budget": "5"},'
        ' {"step": "d", "success_criteria": "ok", "budget": true}]'
    )
    steps = _parse_plan_steps(raw)
    assert steps is not None
    assert all(s.budget is None for s in steps)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no json here",
        "[]",  # empty array
        '[{"step": "missing criteria"}]',  # no usable entries
        '{"step": "a", "success_criteria": "ok"}',  # dict not array
        "[not valid json",
        '"just a string"',
    ],
)
def test_parse_plan_steps_returns_none_for_unusable(raw: str) -> None:
    assert _parse_plan_steps(raw) is None


def test_single_step_plan_wraps_task() -> None:
    plan = _single_step_plan("buy a laptop")
    assert len(plan.steps) == 1
    assert plan.steps[0].step == "buy a laptop"
    assert "buy a laptop" in plan.steps[0].success_criteria
    assert plan.is_complete() is False


def test_single_step_plan_handles_empty_task() -> None:
    plan = _single_step_plan("")
    assert len(plan.steps) == 1
    assert plan.steps[0].step  # non-empty fallback


def test_llm_planner_returns_plan_from_llm_response() -> None:
    llm = _ScriptedLLM(
        [
            _plan_array_response(
                [
                    {"step": "open homepage", "success_criteria": "page loaded"},
                    {
                        "step": "search 'laptop'",
                        "success_criteria": ">=1 product visible",
                        "budget": 3,
                    },
                ]
            )
        ]
    )
    planner = LLMPlanner(llm)
    plan = asyncio.run(planner.plan_task("buy a laptop"))
    assert isinstance(plan, Plan)
    assert [s.step for s in plan.steps] == ["open homepage", "search 'laptop'"]
    assert plan.steps[1].budget == 3


def test_llm_planner_passes_task_and_context_to_llm() -> None:
    llm = _ScriptedLLM(
        [_plan_array_response([{"step": "x", "success_criteria": "y"}])]
    )
    planner = LLMPlanner(llm)
    asyncio.run(planner.plan_task("the original task", "site: example.com"))
    user_prompt = llm.calls[0]["messages"][0]["content"]
    assert "the original task" in user_prompt
    assert "site: example.com" in user_prompt


def test_llm_planner_omits_context_section_when_empty() -> None:
    llm = _ScriptedLLM(
        [_plan_array_response([{"step": "x", "success_criteria": "y"}])]
    )
    planner = LLMPlanner(llm)
    asyncio.run(planner.plan_task("the task"))
    user_prompt = llm.calls[0]["messages"][0]["content"]
    assert "Environment context" not in user_prompt
    assert "the task" in user_prompt


def test_llm_planner_falls_back_to_single_step_on_empty_task() -> None:
    llm = _ScriptedLLM([])  # should not be called
    planner = LLMPlanner(llm)
    plan = asyncio.run(planner.plan_task(""))
    assert len(plan.steps) == 1
    assert llm.calls == []


def test_llm_planner_falls_back_to_single_step_on_malformed_reply() -> None:
    llm = _ScriptedLLM([_text_response("not json at all { garbage")])
    planner = LLMPlanner(llm)
    plan = asyncio.run(planner.plan_task("a complex task"))
    assert len(plan.steps) == 1
    assert plan.steps[0].step == "a complex task"


def test_llm_planner_falls_back_to_single_step_on_empty_array() -> None:
    llm = _ScriptedLLM([_text_response("[]")])
    planner = LLMPlanner(llm)
    plan = asyncio.run(planner.plan_task("a task"))
    assert len(plan.steps) == 1
    assert plan.steps[0].step == "a task"


def test_llm_planner_falls_back_to_single_step_on_exception() -> None:
    class _BoomLLM:
        async def create(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("upstream is down")

    planner = LLMPlanner(_BoomLLM())  # type: ignore[arg-type]
    plan = asyncio.run(planner.plan_task("a task"))
    assert len(plan.steps) == 1
    assert plan.steps[0].step == "a task"


def test_llm_planner_drops_steps_that_lack_required_fields() -> None:
    llm = _ScriptedLLM(
        [
            _plan_array_response(
                [
                    {"step": "good", "success_criteria": "ok"},
                    {"step": "missing criteria"},
                ]
            )
        ]
    )
    planner = LLMPlanner(llm)
    plan = asyncio.run(planner.plan_task("a task"))
    assert [s.step for s in plan.steps] == ["good"]
