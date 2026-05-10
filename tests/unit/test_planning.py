"""Tests for the Plan / Verifier scaffolding (roadmap #7 stage 1).

Stage 1 is data-structures + Protocol only — no LLM calls, no loop wiring.
These tests lock the surface so stages 2/3 can lean on it.
"""
from __future__ import annotations

import asyncio

import pytest

from openmimi.planning import (
    NullVerifier,
    Plan,
    PlanStep,
    Verifier,
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
