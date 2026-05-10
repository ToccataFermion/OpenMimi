"""Plan / Verifier scaffolding for the Planner-Executor-Verifier triangle.

Stage 1 of roadmap item #7. Defines the data structures and the Verifier
Protocol; provides a `NullVerifier` no-op so callers can wire the surface
without committing to LLM-driven verification yet. Nothing here calls an
LLM — Stage 2 will add `LLMVerifier`, Stage 3 will add a Planner.

Glossary:
    PlanStep      — one chunk of the user task, with success criteria.
    Plan          — ordered list of PlanSteps + cursor (`current_step`).
    Verifier      — Protocol; `verify(plan, recent_messages)` returns one
                    of "done" / "continue" / "replan".
    NullVerifier  — always returns "continue"; default when planning is off.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


VerifyOutcome = Literal["done", "continue", "replan"]


@dataclass
class PlanStep:
    """One step of a Plan.

    `success_criteria` is the Verifier's source of truth; it should be a
    short natural-language description (e.g. "search results page shows
    >=3 products with prices visible").
    `allowed_tools` optionally restricts which tools the Executor can use
    during this step (None = inherit from the loop). `budget` is an
    advisory turn cap; the loop may treat overruns as `replan`.
    """

    step: str
    success_criteria: str
    allowed_tools: list[str] | None = None
    budget: int | None = None
    done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "success_criteria": self.success_criteria,
            "allowed_tools": self.allowed_tools,
            "budget": self.budget,
            "done": self.done,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStep:
        return cls(
            step=str(data["step"]),
            success_criteria=str(data["success_criteria"]),
            allowed_tools=data.get("allowed_tools"),
            budget=data.get("budget"),
            done=bool(data.get("done", False)),
        )


@dataclass
class Plan:
    """Ordered PlanStep list with a cursor.

    `current_step` indexes into `steps`; advancing past the end means the
    plan is complete. `current()` returns None when complete so callers
    can treat plan-completion uniformly.
    """

    steps: list[PlanStep] = field(default_factory=list)
    current_step: int = 0

    def current(self) -> PlanStep | None:
        if 0 <= self.current_step < len(self.steps):
            return self.steps[self.current_step]
        return None

    def advance(self) -> None:
        step = self.current()
        if step is not None:
            step.done = True
            self.current_step += 1

    def is_complete(self) -> bool:
        return self.current_step >= len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "current_step": self.current_step,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        steps_raw = data.get("steps", [])
        return cls(
            steps=[PlanStep.from_dict(s) for s in steps_raw],
            current_step=int(data.get("current_step", 0)),
        )


@runtime_checkable
class Verifier(Protocol):
    """Decide whether the Executor should advance, continue, or replan.

    Implementations may inspect `plan` (notably `plan.current()`'s
    `success_criteria`) and `recent_messages` (the last few Anthropic-format
    turns) to make their decision. Stage 1 provides only the no-op
    `NullVerifier`; Stage 2 will add an LLM-backed implementation.
    """

    async def verify(
        self,
        plan: Plan,
        recent_messages: list[dict[str, Any]],
    ) -> VerifyOutcome: ...


class NullVerifier:
    """Default Verifier: always says "continue", never advances or replans.

    Wiring callers can use this as the safe default when
    `AppConfig.enable_planning` is False — the surface stays uniform
    without any LLM cost.
    """

    async def verify(
        self,
        plan: Plan,
        recent_messages: list[dict[str, Any]],
    ) -> VerifyOutcome:
        return "continue"


__all__ = [
    "Plan",
    "PlanStep",
    "Verifier",
    "NullVerifier",
    "VerifyOutcome",
]
