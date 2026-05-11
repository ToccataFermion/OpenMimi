"""Plan / Verifier scaffolding for the Planner-Executor-Verifier triangle.

Roadmap item #7. Stage 1 shipped the data structures and the Verifier
Protocol + `NullVerifier` (always "continue") as a safe default.
Stage 2 wired both into `sampling_loop`. Stage 3a added an LLM-backed
verifier (`LLMVerifier`). Stage 3b adds an LLM-backed planner
(`LLMPlanner`) that decomposes a task into PlanSteps.

Glossary:
    PlanStep      — one chunk of the user task, with success criteria.
    Plan          — ordered list of PlanSteps + cursor (`current_step`).
    Verifier      — Protocol; `verify(plan, recent_messages)` returns one
                    of "done" / "continue" / "replan".
    NullVerifier  — always returns "continue"; default when planning is off.
    LLMVerifier   — asks an LLM to grade progress against `success_criteria`.
    LLMPlanner    — asks an LLM to decompose a task into PlanSteps.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from .llm.base import LLMClient


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

    def format_progress(self) -> str:
        """Human-readable progress bar for terminal output.

        Returns an empty string when there are no steps.
        """
        if not self.steps:
            return ""
        lines: list[str] = []
        total = len(self.steps)
        for i, s in enumerate(self.steps, 1):
            marker = "[x]" if s.done else "[ ]"
            emphasis = " → " if i - 1 == self.current_step and not s.done else "   "
            lines.append(f"{emphasis}{marker} {i}. {s.step}")
        return "\n".join(lines)

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


_LLM_VERIFIER_SYSTEM = (
    "You are a plan-progress verifier for an autonomous agent. "
    "Your job is to check whether the ENTIRE user task is fully finished, "
    "not just the current step. "
    "Return 'done' ONLY when every planned step has been completed. "
    "If the current step is finished but later steps remain, return 'continue'. "
    "Return 'replan' ONLY when one of the following is true:\n"
    "  1) The agent has failed the SAME step 3 or more times in a row (persistent failure).\n"
    "  2) The agent is clearly working on the WRONG task entirely (goal drift).\n"
    "DO NOT return 'replan' for transient issues such as a single failed click, "
    "element not found, or network timeout — the executor has built-in retry and "
    "will recover automatically. Most temporary errors should yield 'continue'.\n"
    "Output STRICT JSON only with shape "
    '{"outcome": "done|continue|replan", "reason": "<one sentence>"} — '
    "no prose, no markdown, no fences."
)


_OUTCOMES: tuple[VerifyOutcome, ...] = ("done", "continue", "replan")


def _extract_response_text(response: dict[str, Any]) -> str:
    """Pull text content out of an LLMClient.create() response."""
    content = response.get("content") or []
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _parse_outcome(text: str) -> VerifyOutcome | None:
    """Extract a VerifyOutcome from a possibly-noisy LLM reply.

    Tries direct JSON first, then a regex sniff for the first {...} blob.
    Returns None if no valid outcome string was found.
    """
    if not text:
        return None
    candidates = [text.strip()]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            outcome = data.get("outcome")
            if isinstance(outcome, str) and outcome in _OUTCOMES:
                return outcome  # type: ignore[return-value]
    return None


def _count_consecutive_errors(messages: list[dict[str, Any]]) -> int:
    """Count how many recent tool_result blocks have ``is_error=True``.

    Walks backwards from the end of ``messages`` and counts contiguous
    ``tool_result`` blocks with ``is_error=True``. Stops at the first
    non-error tool_result or non-tool_result message.
    """
    count = 0
    for msg in reversed(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            if block.get("is_error"):
                count += 1
            else:
                return count
    return count


def _summarize_messages(
    messages: list[dict[str, Any]], max_chars_per_block: int = 400
) -> str:
    """Condense recent agent turns into a compact, prompt-friendly summary.

    Walks each message's content blocks; keeps text and surfaces tool_use /
    tool_result types as one-line markers. Truncates each text block to
    `max_chars_per_block` to keep the verifier prompt small.
    """
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str):
            snippet = content[:max_chars_per_block]
            lines.append(f"[{role}] {snippet}")
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                txt = block.get("text", "")
                lines.append(f"[{role}/text] {txt[:max_chars_per_block]}")
            elif btype == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                try:
                    inp_s = json.dumps(inp, ensure_ascii=False)
                except (TypeError, ValueError):
                    inp_s = str(inp)
                lines.append(
                    f"[{role}/tool_use] {name}({inp_s[:max_chars_per_block]})"
                )
            elif btype == "tool_result":
                err = " ERROR" if block.get("is_error") else ""
                sub = block.get("content")
                texts: list[str] = []
                if isinstance(sub, list):
                    for s in sub:
                        if isinstance(s, dict) and s.get("type") == "text":
                            texts.append(str(s.get("text", "")))
                summary = " | ".join(texts) if texts else ""
                lines.append(
                    f"[{role}/tool_result{err}] {summary[:max_chars_per_block]}"
                )
            elif btype == "image":
                lines.append(f"[{role}/image]")
    return "\n".join(lines)


class LLMVerifier:
    """Verifier that asks an LLM to grade the current PlanStep.

    Uses the supplied `LLMClient` (caller picks the model — typically a
    cheaper one like Haiku) with a strict-JSON system prompt. Any failure
    along the path — empty `plan.current()` notwithstanding — falls back
    to "continue" so a flaky verifier never aborts a session. Empty
    `plan.current()` returns "done" because there's nothing left to check.
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_tail_messages: int = 6,
        max_tokens: int = 200,
        system_prompt: str | None = None,
    ) -> None:
        self.llm = llm
        self.max_tail_messages = max_tail_messages
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt or _LLM_VERIFIER_SYSTEM

    async def verify(
        self,
        plan: Plan,
        recent_messages: list[dict[str, Any]],
    ) -> VerifyOutcome:
        current = plan.current()
        if current is None:
            return "done"
        tail = (
            recent_messages[-self.max_tail_messages:]
            if recent_messages
            else []
        )
        total_steps = len(plan.steps)
        current_idx = plan.current_step + 1
        plan_summary = "\n".join(
            f"  {i + 1}. {s.step} [{'DONE' if s.done else 'PENDING'}]"
            for i, s in enumerate(plan.steps)
        )
        recent_errors = _count_consecutive_errors(tail)
        user_prompt = (
            f"Plan ({current_idx} of {total_steps} steps):\n"
            f"{plan_summary}\n\n"
            f"Current step: {current.step}\n"
            f"Success criteria: {current.success_criteria}\n"
            f"Consecutive failed attempts at this step: {recent_errors}\n\n"
            f"Recent agent activity (oldest first):\n"
            f"{_summarize_messages(tail) or '(no activity)'}\n\n"
            "Instructions: Return 'done' ONLY if ALL steps above are marked DONE. "
            "If the current step is finished but later steps are still PENDING, return 'continue'. "
            "Return 'replan' ONLY if there are 3+ consecutive failures for this step or clear goal drift. "
            'Reply with ONLY: {"outcome": "done|continue|replan", "reason": "..."}'
        )
        try:
            response = await self.llm.create(
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[],
                max_tokens=self.max_tokens,
            )
        except Exception:
            return "continue"
        text = _extract_response_text(response)
        outcome = _parse_outcome(text)
        return outcome if outcome is not None else "continue"


_LLM_PLANNER_SYSTEM = (
    "You are a task-decomposition planner for an autonomous browser agent. "
    "Given a user task and optional environment context, output a short "
    "ordered plan that an executor can run step by step. "
    "Each step must be observable (the verifier needs to grade success). "
    "Output STRICT JSON only — a top-level array of step objects, each with: "
    '"step" (imperative sentence), '
    '"success_criteria" (one short sentence the verifier can check), '
    'optional "allowed_tools" (array of tool names — omit if unrestricted), '
    'optional "budget" (integer turn cap for this step). '
    "Prefer 1-5 steps for simple tasks, up to ~8 for complex ones. "
    "No prose, no markdown, no fences — only the JSON array."
)


def _parse_plan_steps(text: str) -> list[PlanStep] | None:
    """Extract a list[PlanStep] from a possibly-noisy LLM reply.

    Tries direct JSON first, then a regex sniff for the first [...] blob.
    Each candidate dict must have non-empty `step` and `success_criteria`
    strings; malformed entries are dropped. Returns None if no usable
    steps survive (caller should fall back to a single-step plan).
    """
    if not text:
        return None
    candidates = [text.strip()]
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        steps: list[PlanStep] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            step = entry.get("step")
            criteria = entry.get("success_criteria")
            if not isinstance(step, str) or not step.strip():
                continue
            if not isinstance(criteria, str) or not criteria.strip():
                continue
            allowed = entry.get("allowed_tools")
            allowed_tools: list[str] | None = None
            if isinstance(allowed, list):
                allowed_tools = [
                    str(t) for t in allowed if isinstance(t, str) and t
                ] or None
            budget_raw = entry.get("budget")
            budget: int | None = None
            if isinstance(budget_raw, bool):
                pass  # bool is int — ignore on purpose
            elif isinstance(budget_raw, int) and budget_raw > 0:
                budget = budget_raw
            steps.append(
                PlanStep(
                    step=step.strip(),
                    success_criteria=criteria.strip(),
                    allowed_tools=allowed_tools,
                    budget=budget,
                )
            )
        if steps:
            return steps
    return None


def _single_step_plan(task: str) -> Plan:
    """Fallback Plan used when the Planner LLM fails or returns garbage.

    Wraps the whole task as a single step so the rest of the pipeline
    still has something to work with — the Verifier will keep returning
    "continue" until the loop reaches its existing turn cap or the
    Verifier flips to "done" / "replan".
    """
    task = task.strip() or "complete the user's request"
    return Plan(
        steps=[
            PlanStep(
                step=task,
                success_criteria=f"task accomplished: {task}",
            )
        ]
    )


class LLMPlanner:
    """Planner that asks an LLM to decompose a task into PlanSteps.

    Mirrors `LLMVerifier`'s safety model: any failure (network, JSON
    parse, empty array) falls back to a single-step plan that wraps
    the whole task. `plan_task` therefore **always** returns a usable
    `Plan` — never None, never raises. Caller picks the underlying
    model via the `LLMClient` it injects.
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_tokens: int = 1024,
        system_prompt: str | None = None,
    ) -> None:
        self.llm = llm
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt or _LLM_PLANNER_SYSTEM

    async def plan_task(self, task: str, system_context: str = "") -> Plan:
        clean_task = (task or "").strip()
        if not clean_task:
            return _single_step_plan("complete the user's request")
        ctx = (system_context or "").strip()
        user_prompt = (
            (f"Environment context:\n{ctx}\n\n" if ctx else "")
            + f"User task:\n{clean_task}\n\n"
            + 'Reply with ONLY a JSON array like '
            + '[{"step": "...", "success_criteria": "...", '
            + '"allowed_tools": ["..."], "budget": 5}, ...]'
        )
        try:
            response = await self.llm.create(
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[],
                max_tokens=self.max_tokens,
            )
        except Exception:
            return _single_step_plan(clean_task)
        text = _extract_response_text(response)
        steps = _parse_plan_steps(text)
        if not steps:
            return _single_step_plan(clean_task)
        return Plan(steps=steps)


__all__ = [
    "LLMPlanner",
    "LLMVerifier",
    "NullVerifier",
    "Plan",
    "PlanStep",
    "Verifier",
    "VerifyOutcome",
]
