"""Unit tests for the sampling loop.

The tests use fake LLM / tool / audit doubles to keep the loop fully
deterministic and offline.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest

from openmimi.loop import sampling_loop
from openmimi.tools.base import ToolBase
from openmimi.tools.collection import ToolCollection
from openmimi.tools.errors import ErrorCode
from openmimi.tools.result import ToolResult

_FAKE_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepng").decode()


class _ScriptedLLM:
    """LLM stub that returns one preset response per call, in order."""

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
                "messages": [dict(m) for m in messages],
                "tools": tools,
                "max_tokens": max_tokens,
            }
        )
        if not self._responses:
            raise AssertionError("ScriptedLLM ran out of responses")
        return self._responses.pop(0)


class _FakeTool(ToolBase):
    """Tool stub whose behaviour is defined by a callable."""

    name = "browser"

    def __init__(self, behaviour: Any) -> None:
        self._behaviour = behaviour
        self.received: list[dict[str, Any]] = []

    def to_params(self) -> dict[str, Any]:
        return {"name": self.name, "description": "fake", "input_schema": {}}

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        self.received.append(tool_input)
        outcome = (
            self._behaviour(tool_input)
            if callable(self._behaviour)
            else self._behaviour
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _RecordingAudit:
    def __init__(self) -> None:
        self.logs: list[dict[str, Any]] = []
        self.screenshots: list[dict[str, Any]] = []

    def save_screenshot(
        self, *, session_id: str, step: int, png_bytes: bytes
    ) -> str:
        self.screenshots.append(
            {"session_id": session_id, "step": step, "size": len(png_bytes)}
        )
        return f"/screens/{session_id}/step_{step}.png"

    def log_tool_call(
        self,
        *,
        session_id: str,
        step: int,
        tool: str,
        tool_input: dict[str, Any],
        result_summary: str,
        is_error: bool,
        error_code: str | None,
        image_path: str | None,
        duration_ms: int,
    ) -> None:
        self.logs.append(
            {
                "session_id": session_id,
                "step": step,
                "tool": tool,
                "tool_input": tool_input,
                "result_summary": result_summary,
                "is_error": is_error,
                "error_code": error_code,
                "image_path": image_path,
                "duration_ms": duration_ms,
            }
        )


def _assistant(content: list[dict[str, Any]], stop_reason: str) -> dict[str, Any]:
    return {"role": "assistant", "content": content, "stop_reason": stop_reason}


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _tool_use(block_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": block_id, "name": name, "input": tool_input}


def _make_collection(tool: ToolBase) -> ToolCollection:
    coll = ToolCollection()
    coll.register(tool)
    return coll


@pytest.mark.asyncio
async def test_end_turn_returns_immediately() -> None:
    llm = _ScriptedLLM([_assistant([_text("done")], stop_reason="end_turn")])
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)

    messages: list[dict[str, Any]] = [{"role": "user", "content": "say hi"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="sess-1",
    )

    assert out is messages
    assert len(out) == 2
    assert out[-1]["role"] == "assistant"
    assert tool.received == []
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_single_tool_use_then_end_turn() -> None:
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("all good")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(
        ToolResult(output="screenshot taken", base64_image=_FAKE_PNG_B64)
    )
    coll = _make_collection(tool)
    audit = _RecordingAudit()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="sess-1",
        audit=audit,
    )

    assert len(out) == 4
    assert out[1]["role"] == "assistant"
    assert out[2]["role"] == "user"
    assert out[3]["role"] == "assistant"

    tr_blocks = out[2]["content"]
    assert len(tr_blocks) == 1
    tr = tr_blocks[0]
    assert tr["type"] == "tool_result"
    assert tr["tool_use_id"] == "tu1"
    assert tr.get("is_error") is None
    sub_types = [c["type"] for c in tr["content"]]
    assert sub_types == ["text", "image"]

    assert tool.received == [{"action": "screenshot"}]
    assert len(audit.logs) == 1
    log = audit.logs[0]
    assert log["tool"] == "browser"
    assert log["is_error"] is False
    assert log["image_path"] == "/screens/sess-1/step_1.png"
    assert log["error_code"] is None
    assert audit.screenshots == [
        {
            "session_id": "sess-1",
            "step": 1,
            "size": len(base64.b64decode(_FAKE_PNG_B64)),
        }
    ]


@pytest.mark.asyncio
async def test_multiple_tool_uses_in_one_assistant_message() -> None:
    llm = _ScriptedLLM(
        [
            _assistant(
                [
                    _text("Working on it"),
                    _tool_use("a", "browser", {"action": "screenshot"}),
                    _tool_use("b", "browser", {"action": "wait", "duration_s": 1}),
                ],
                stop_reason="tool_use",
            ),
            _assistant([_text("finished")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages, tools=coll, llm=llm, session_id="s"
    )

    user_msg = out[2]
    assert user_msg["role"] == "user"
    assert [b["tool_use_id"] for b in user_msg["content"]] == ["a", "b"]
    assert len(tool.received) == 2


@pytest.mark.asyncio
async def test_tool_exception_becomes_error_result() -> None:
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("recovered")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(RuntimeError("boom"))
    coll = _make_collection(tool)
    audit = _RecordingAudit()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        audit=audit,
    )

    tr = out[2]["content"][0]
    assert tr["is_error"] is True
    assert "RuntimeError" in tr["content"][0]["text"]

    assert len(audit.logs) == 1
    log = audit.logs[0]
    assert log["is_error"] is True
    assert log["error_code"] == ErrorCode.TOOL_INTERNAL_ERROR.value
    assert log["image_path"] is None


@pytest.mark.asyncio
async def test_tool_result_with_is_error_flag() -> None:
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "click"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("end")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(
        ToolResult(
            output="not found",
            is_error=True,
            details={"error_code": ErrorCode.TARGET_NOT_FOUND.value},
        )
    )
    coll = _make_collection(tool)
    audit = _RecordingAudit()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages, tools=coll, llm=llm, session_id="s", audit=audit
    )

    assert out[2]["content"][0]["is_error"] is True
    assert audit.logs[0]["error_code"] == "TARGET_NOT_FOUND"
    assert audit.logs[0]["is_error"] is True


@pytest.mark.asyncio
async def test_max_turns_caps_loop() -> None:
    def _always_tool() -> dict[str, Any]:
        return _assistant(
            [_tool_use("x", "browser", {"action": "screenshot"})],
            stop_reason="tool_use",
        )

    llm = _ScriptedLLM([_always_tool() for _ in range(10)])
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        max_turns=3,
    )

    assert len(llm.calls) == 3
    assistant_count = sum(1 for m in out if m.get("role") == "assistant")
    assert assistant_count == 3
    assert len(tool.received) == 3


@pytest.mark.asyncio
async def test_old_images_are_trimmed() -> None:
    def _tool_then_done(turn: int) -> dict[str, Any]:
        if turn < 3:
            return _assistant(
                [_tool_use(f"id-{turn}", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            )
        return _assistant([_text("done")], stop_reason="end_turn")

    llm = _ScriptedLLM([_tool_then_done(t) for t in range(4)])
    tool = _FakeTool(ToolResult(output="ok", base64_image=_FAKE_PNG_B64))
    coll = _make_collection(tool)

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        only_n_most_recent_images=1,
    )

    image_blocks: list[dict[str, Any]] = []
    text_stubs: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            for item in block.get("content", []):
                if item.get("type") == "image":
                    image_blocks.append(item)
                elif (
                    item.get("type") == "text"
                    and item.get("text") == "[image omitted to save context]"
                ):
                    text_stubs.append(item)

    assert len(image_blocks) == 1
    assert len(text_stubs) == 2


@pytest.mark.asyncio
async def test_empty_tool_name_becomes_protocol_error() -> None:
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "", {"raw_arguments": '{"action": "click"'})],
                stop_reason="tool_use",
            ),
            _assistant([_text("recovered")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)
    audit = _RecordingAudit()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        audit=audit,
    )

    tr = out[2]["content"][0]
    assert tr["is_error"] is True
    assert "missing 'name'" in tr["content"][0]["text"]
    assert tool.received == []

    assert len(audit.logs) == 1
    log = audit.logs[0]
    assert log["tool"] == "<unknown>"
    assert log["is_error"] is True
    assert log["error_code"] == ErrorCode.TOOL_INTERNAL_ERROR.value


@pytest.mark.asyncio
async def test_raw_arguments_only_input_becomes_protocol_error() -> None:
    llm = _ScriptedLLM(
        [
            _assistant(
                [
                    _tool_use(
                        "tu1",
                        "browser",
                        {"raw_arguments": '{"action": "click", "coor'},
                    )
                ],
                stop_reason="tool_use",
            ),
            _assistant([_text("recovered")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)
    audit = _RecordingAudit()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        audit=audit,
    )

    tr = out[2]["content"][0]
    assert tr["is_error"] is True
    assert "raw_arguments" in tr["content"][0]["text"]
    assert tool.received == []

    log = audit.logs[0]
    assert log["tool"] == "browser"
    assert log["error_code"] == ErrorCode.TOOL_INTERNAL_ERROR.value


class _SlowBrowserTool(ToolBase):
    name = "browser"

    def to_params(self) -> dict[str, Any]:
        return {"name": self.name, "description": "slow", "input_schema": {}}

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        await asyncio.sleep(30.0)
        return ToolResult(output="never")


@pytest.mark.asyncio
async def test_tool_invocation_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    import openmimi.loop as loop_mod

    monkeypatch.setattr(loop_mod, "_tool_run_timeout_seconds", lambda: 0.12)

    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("t1", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("recovered")], stop_reason="end_turn"),
        ]
    )
    coll = _make_collection(_SlowBrowserTool())
    audit = _RecordingAudit()
    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages, tools=coll, llm=llm, session_id="s", audit=audit
    )

    assert len(audit.logs) == 1
    assert audit.logs[0]["is_error"] is True
    assert "timed out" in (audit.logs[0]["result_summary"] or "").lower()
    tr = out[2]["content"][0]
    assert tr["is_error"] is True
    assert out[-1]["role"] == "assistant"
    last_parts = [
        b.get("text", "")
        for b in (out[-1].get("content") or [])
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    assert "recovered" in "\n".join(last_parts)


@pytest.mark.asyncio
async def test_audit_optional() -> None:
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("done")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages, tools=coll, llm=llm, session_id="s"
    )
    assert len(out) == 4


# --- Verifier wiring (#7 stage 2) -------------------------------------------

from openmimi.planning import NullVerifier, Plan, PlanStep


class _RecordingVerifier:
    """Records every verify() call and returns scripted outcomes."""

    def __init__(self, outcomes: list[str]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[Plan, int]] = []

    async def verify(
        self, plan: Plan, recent_messages: list[dict[str, Any]]
    ) -> str:
        self.calls.append((plan, len(recent_messages)))
        if not self._outcomes:
            return "continue"
        return self._outcomes.pop(0)


def _make_two_turn_llm() -> _ScriptedLLM:
    """LLM that does two browser turns then ends. Used for verifier tests."""
    return _ScriptedLLM(
        [
            _assistant(
                [_tool_use("a", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            ),
            _assistant(
                [_tool_use("b", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("end")], stop_reason="end_turn"),
        ]
    )


@pytest.mark.asyncio
async def test_no_verifier_means_no_invocation() -> None:
    """When verifier+plan are both None the loop must run unchanged."""
    llm = _make_two_turn_llm()
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages, tools=coll, llm=llm, session_id="s"
    )

    # 1 user + 3 assistant + 2 user(tool_results) = 6 messages
    assert len(out) == 6
    assert out[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_verifier_without_plan_is_skipped() -> None:
    """Verifier is provided but plan is None → fast-path skip."""
    llm = _make_two_turn_llm()
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)

    verifier = _RecordingVerifier(["done"])  # would short-circuit if invoked

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        verifier=verifier,
        plan=None,
    )

    assert verifier.calls == []
    assert len(out) == 6  # ran to natural end_turn, ignoring "done" outcome


@pytest.mark.asyncio
async def test_null_verifier_lets_loop_continue_normally() -> None:
    """NullVerifier always returns 'continue' → loop runs to natural end."""
    llm = _make_two_turn_llm()
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)
    verifier = NullVerifier()
    plan = Plan(
        steps=[PlanStep(step="screenshot the page", success_criteria="image taken")]
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        verifier=verifier,
        plan=plan,
    )

    # NullVerifier never says "done" → all 3 LLM turns happen.
    assert len(out) == 6


@pytest.mark.asyncio
async def test_verifier_done_ends_loop_early() -> None:
    """verifier returning 'done' on turn 1 ends the loop before turn 2."""
    llm = _make_two_turn_llm()
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)
    verifier = _RecordingVerifier(["done"])
    plan = Plan(steps=[PlanStep(step="capture", success_criteria="png")])

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        verifier=verifier,
        plan=plan,
    )

    assert len(verifier.calls) == 1
    assert len(llm.calls) == 1  # only the first turn ran
    # 1 user + 1 assistant (turn 1) + 1 user(tool_results) = 3 messages
    assert len(out) == 3


@pytest.mark.asyncio
async def test_verifier_replan_does_not_end_loop() -> None:
    """'replan' is a stage-3 hook; for stage 2 the loop just keeps going."""
    llm = _make_two_turn_llm()
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)
    verifier = _RecordingVerifier(["replan", "continue"])
    plan = Plan(steps=[PlanStep(step="capture", success_criteria="png")])

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        verifier=verifier,
        plan=plan,
    )

    assert len(verifier.calls) == 2  # one per turn (3 LLM turns, 2 with tools)
    assert len(out) == 6


@pytest.mark.asyncio
async def test_verifier_exception_does_not_crash_loop() -> None:
    """A verifier that raises should be logged-and-ignored, not propagated."""

    class _ExplodingVerifier:
        def __init__(self) -> None:
            self.calls = 0

        async def verify(
            self, plan: Plan, recent_messages: list[dict[str, Any]]
        ) -> str:
            self.calls += 1
            raise RuntimeError("verifier exploded")

    llm = _make_two_turn_llm()
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)
    verifier = _ExplodingVerifier()
    plan = Plan(steps=[PlanStep(step="capture", success_criteria="png")])

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        verifier=verifier,
        plan=plan,
    )

    assert verifier.calls == 2  # invoked on each of the 2 tool-result turns
    assert len(out) == 6  # loop ran to natural end despite exceptions


@pytest.mark.asyncio
async def test_verifier_skipped_when_plan_complete() -> None:
    """An already-complete Plan should bypass verifier invocation entirely."""
    llm = _make_two_turn_llm()
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)
    verifier = _RecordingVerifier(["done"])  # would short-circuit if invoked
    plan = Plan(steps=[])  # empty plan = is_complete() True

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        verifier=verifier,
        plan=plan,
    )

    assert verifier.calls == []
    assert len(out) == 6


# --- Token-budget / compression (roadmap #5 stage 1) -----------------------


def test_appconfig_defaults_max_context_tokens_and_strategy() -> None:
    """Stage 1 ships only schema fields; defaults must preserve legacy behavior."""
    from openmimi.config.schema import AppConfig

    cfg = AppConfig()
    assert cfg.max_context_tokens == 80000
    assert cfg.compression_strategy == "truncate"


def test_appconfig_compression_strategy_rejects_invalid() -> None:
    """The Literal type should validate the strategy string."""
    from pydantic import ValidationError

    from openmimi.config.schema import AppConfig

    with pytest.raises(ValidationError):
        AppConfig(compression_strategy="summary")  # type: ignore[arg-type]


def test_appconfig_compression_strategy_accepts_summarize() -> None:
    from openmimi.config.schema import AppConfig

    cfg = AppConfig(compression_strategy="summarize")
    assert cfg.compression_strategy == "summarize"


# --- compress_tool_result / estimate_tokens (roadmap #5 stage 2) ----------


def test_estimate_tokens_returns_zero_for_empty() -> None:
    from openmimi.compression import estimate_tokens

    assert estimate_tokens("") == 0


def test_estimate_tokens_uses_4_char_heuristic() -> None:
    from openmimi.compression import estimate_tokens

    assert estimate_tokens("a" * 16) == 4
    assert estimate_tokens("a" * 100) == 25


def test_estimate_tokens_returns_at_least_one_for_short_text() -> None:
    """`max(1, len // 4)` keeps callers safe from divide-by-zero math."""
    from openmimi.compression import estimate_tokens

    assert estimate_tokens("hi") == 1
    assert estimate_tokens("x") == 1


@pytest.mark.asyncio
async def test_compress_tool_result_returns_short_text_untouched() -> None:
    from openmimi.compression import compress_tool_result

    out = await compress_tool_result("tiny", llm=None, target_chars=500)
    assert out == "tiny"


@pytest.mark.asyncio
async def test_compress_tool_result_truncates_when_no_llm() -> None:
    from openmimi.compression import compress_tool_result

    text = "x" * 2000
    out = await compress_tool_result(text, llm=None, target_chars=100)
    assert out.startswith("x" * 100)
    assert "[truncated" in out


@pytest.mark.asyncio
async def test_compress_tool_result_calls_llm_for_long_text() -> None:
    from openmimi.compression import compress_tool_result

    llm = _ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Did: navigated to homepage\n"
                            "Saw: header + 3 nav links\n"
                            "Data: url=https://x.com, status=200"
                        ),
                    }
                ],
                "stop_reason": "end_turn",
            }
        ]
    )
    text = "y" * 5000
    out = await compress_tool_result(text, llm=llm, target_chars=200)
    assert "Did: navigated" in out
    assert "[compressed by LLM]" in out
    assert len(llm.calls) == 1
    user_prompt = llm.calls[0]["messages"][0]["content"]
    assert "tool output start" in user_prompt
    assert "tool output end" in user_prompt


@pytest.mark.asyncio
async def test_compress_tool_result_falls_back_when_llm_raises() -> None:
    from openmimi.compression import compress_tool_result

    class _BoomLLM:
        async def create(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("upstream is down")

    text = "z" * 3000
    out = await compress_tool_result(text, llm=_BoomLLM(), target_chars=120)
    assert out.startswith("z" * 120)
    assert "[truncated" in out


@pytest.mark.asyncio
async def test_compress_tool_result_falls_back_when_reply_empty() -> None:
    from openmimi.compression import compress_tool_result

    llm = _ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": ""}],
                "stop_reason": "end_turn",
            }
        ]
    )
    text = "w" * 2000
    out = await compress_tool_result(text, llm=llm, target_chars=150)
    assert out.startswith("w" * 150)
    assert "[truncated" in out


@pytest.mark.asyncio
async def test_compress_tool_result_caps_oversized_summary() -> None:
    """If the LLM ignores the 3-line cap, we still bound the output."""
    from openmimi.compression import compress_tool_result

    long_summary = "Did: ok\n" + ("payload " * 500)
    llm = _ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": long_summary}],
                "stop_reason": "end_turn",
            }
        ]
    )
    out = await compress_tool_result(
        "input " * 1000, llm=llm, target_chars=200
    )
    # 2x target_chars cap (400) + suffix
    body = out.replace("\n... [compressed by LLM]", "")
    assert len(body) <= 400


@pytest.mark.asyncio
async def test_compress_tool_result_clips_input_before_sending_to_llm() -> None:
    """A 100k tool_result must not be shipped verbatim to the compressor."""
    from openmimi.compression import compress_tool_result

    llm = _ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Did: x\nSaw: y\nData: z"}
                ],
                "stop_reason": "end_turn",
            }
        ]
    )
    text = "Q" * 100_000
    await compress_tool_result(text, llm=llm, target_chars=300)
    user_prompt = llm.calls[0]["messages"][0]["content"]
    # default max_input_chars=8000; +/- prompt scaffolding stays well below 10000
    assert len(user_prompt) < 10_000


# --- _compress_old_tool_results strategy / budget gate (#5 stage 3) -------


def _msgs_with_old_long_tool_result(text: str) -> list[dict[str, Any]]:
    """Build a 5-message list whose only old user message has a long
    tool_result text block. With ``max_context_turns=0`` the last message
    is kept and the middle three are eligible — the user(tool_result)
    at index 2 is the one that gets compressed."""
    return [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu1",
                    "name": "browser",
                    "input": {"action": "screenshot"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu2",
                    "name": "browser",
                    "input": {"action": "click"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu2",
                    "content": [{"type": "text", "text": "fresh"}],
                }
            ],
        },
    ]


def _extract_old_text(messages: list[dict[str, Any]]) -> str:
    """Pull the text of the old tool_result block (msg index 2)."""
    return messages[2]["content"][0]["content"][0]["text"]


@pytest.mark.asyncio
async def test_compress_old_truncate_strategy_preserves_legacy_400_cut() -> None:
    """``strategy="truncate"`` must hard-cut at 400 chars + legacy suffix."""
    from openmimi.loop import _TRUNCATED_TEXT_SUFFIX, _compress_old_tool_results

    messages = _msgs_with_old_long_tool_result("X" * 5000)
    await _compress_old_tool_results(
        messages,
        max_context_turns=0,
        strategy="truncate",
    )
    out_text = _extract_old_text(messages)
    assert out_text.startswith("X" * 400)
    assert out_text.endswith(_TRUNCATED_TEXT_SUFFIX)
    assert len(out_text) == 400 + len(_TRUNCATED_TEXT_SUFFIX)


@pytest.mark.asyncio
async def test_compress_old_summarize_skips_when_under_budget() -> None:
    """When approx tokens <= ``max_context_tokens`` nothing is touched."""
    from openmimi.loop import _compress_old_tool_results

    original = "X" * 5000
    messages = _msgs_with_old_long_tool_result(original)

    llm = _ScriptedLLM([])  # would crash if invoked
    await _compress_old_tool_results(
        messages,
        max_context_turns=0,
        strategy="summarize",
        max_context_tokens=10_000_000,  # absurdly high → always under budget
        compress_llm=llm,
    )
    assert _extract_old_text(messages) == original
    assert llm.calls == []


@pytest.mark.asyncio
async def test_compress_old_summarize_invokes_llm_when_over_budget() -> None:
    """Over-budget summarize replaces text with a Did/Saw/Data summary."""
    from openmimi.loop import _compress_old_tool_results

    original = "X" * 5000
    messages = _msgs_with_old_long_tool_result(original)

    llm = _ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Did: a\nSaw: b\nData: c"}
                ],
                "stop_reason": "end_turn",
            }
        ]
    )
    await _compress_old_tool_results(
        messages,
        max_context_turns=0,
        strategy="summarize",
        max_context_tokens=10,  # tiny → always over budget
        compress_llm=llm,
        summarize_target_chars=300,
    )
    out_text = _extract_old_text(messages)
    assert "[compressed by LLM]" in out_text
    assert "Did: a" in out_text
    assert len(out_text) < len(original)
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_compress_old_summarize_idempotent_on_second_pass() -> None:
    """Already-compressed blocks must not be re-sent to the LLM."""
    from openmimi.loop import _compress_old_tool_results

    messages = _msgs_with_old_long_tool_result("X" * 5000)

    llm = _ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Did: a\nSaw: b\nData: c"}
                ],
                "stop_reason": "end_turn",
            }
        ]
    )
    # First pass — should compress.
    await _compress_old_tool_results(
        messages,
        max_context_turns=0,
        strategy="summarize",
        max_context_tokens=10,
        compress_llm=llm,
    )
    assert len(llm.calls) == 1
    after_first = _extract_old_text(messages)

    # Second pass — should be a no-op (no new LLM calls, text unchanged).
    await _compress_old_tool_results(
        messages,
        max_context_turns=0,
        strategy="summarize",
        max_context_tokens=10,
        compress_llm=llm,
    )
    assert len(llm.calls) == 1
    assert _extract_old_text(messages) == after_first


@pytest.mark.asyncio
async def test_compress_old_summarize_no_llm_falls_back_to_truncate() -> None:
    """``compress_llm=None`` over-budget falls back to legacy truncation."""
    from openmimi.loop import _TRUNCATED_TEXT_SUFFIX, _compress_old_tool_results

    messages = _msgs_with_old_long_tool_result("X" * 5000)
    await _compress_old_tool_results(
        messages,
        max_context_turns=0,
        strategy="summarize",
        max_context_tokens=10,
        compress_llm=None,
        summarize_target_chars=200,
    )
    out_text = _extract_old_text(messages)
    assert out_text.endswith(_TRUNCATED_TEXT_SUFFIX)
    assert "[compressed by LLM]" not in out_text


@pytest.mark.asyncio
async def test_compress_old_truncate_idempotent_on_already_truncated() -> None:
    """Truncate strategy also skips blocks already carrying the suffix."""
    from openmimi.loop import _TRUNCATED_TEXT_SUFFIX, _compress_old_tool_results

    pre_truncated = "Y" * 100 + _TRUNCATED_TEXT_SUFFIX
    messages = _msgs_with_old_long_tool_result(pre_truncated)
    await _compress_old_tool_results(
        messages,
        max_context_turns=0,
        strategy="truncate",
    )
    # No re-truncation: the suffix appears exactly once.
    assert _extract_old_text(messages).count(_TRUNCATED_TEXT_SUFFIX) == 1


# --- Episodic memory wiring (#9 stage 2) ------------------------------------


class _RecordingEpisodic:
    """Captures every ``append_step`` call for assertion."""

    def __init__(self, *, behaviour: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._behaviour = behaviour

    def append_step(
        self,
        *,
        session_id: str,
        step: int,
        record: dict[str, Any],
    ) -> Any:
        self.calls.append(
            {"session_id": session_id, "step": step, "record": dict(record)}
        )
        if isinstance(self._behaviour, Exception):
            raise self._behaviour
        if callable(self._behaviour):
            self._behaviour()
        return None


@pytest.mark.asyncio
async def test_episodic_records_successful_tool_call() -> None:
    """Each successful tool call writes one parallel step record."""
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("done")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)
    episodic = _RecordingEpisodic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="sess-A",
        episodic=episodic,
    )

    assert len(episodic.calls) == 1
    call = episodic.calls[0]
    assert call["session_id"] == "sess-A"
    assert call["step"] == 1
    rec = call["record"]
    assert rec["tool"] == "browser"
    assert rec["action"] == "screenshot"  # extracted from tool_input
    assert rec["result_summary"] == "ok"
    assert rec["is_error"] is False
    assert rec["error_code"] is None
    assert rec["url"] is None
    assert rec["domain"] is None


@pytest.mark.asyncio
async def test_episodic_extracts_url_and_domain_from_details() -> None:
    """When ToolResult.details carries url, episodic record gets url+domain."""
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "navigate"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("end")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(
        ToolResult(
            output="navigated",
            details={"url": "https://example.com/foo"},
        )
    )
    coll = _make_collection(tool)
    episodic = _RecordingEpisodic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        episodic=episodic,
    )

    rec = episodic.calls[0]["record"]
    assert rec["url"] == "https://example.com/foo"
    assert rec["domain"] == "example.com"


@pytest.mark.asyncio
async def test_episodic_records_tool_error_result() -> None:
    """A ToolResult with is_error=True propagates is_error/error_code."""
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "click"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("end")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(
        ToolResult(
            output="not found",
            is_error=True,
            details={"error_code": ErrorCode.TARGET_NOT_FOUND.value},
        )
    )
    coll = _make_collection(tool)
    episodic = _RecordingEpisodic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        episodic=episodic,
    )

    rec = episodic.calls[0]["record"]
    assert rec["is_error"] is True
    assert rec["error_code"] == "TARGET_NOT_FOUND"
    assert rec["action"] == "click"


@pytest.mark.asyncio
async def test_episodic_records_tool_exception() -> None:
    """A raised exception during tool dispatch still emits one record."""
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("recovered")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(RuntimeError("boom"))
    coll = _make_collection(tool)
    episodic = _RecordingEpisodic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        episodic=episodic,
    )

    assert len(episodic.calls) == 1
    rec = episodic.calls[0]["record"]
    assert rec["is_error"] is True
    assert rec["error_code"] == ErrorCode.TOOL_INTERNAL_ERROR.value
    assert "RuntimeError" in rec["result_summary"]


@pytest.mark.asyncio
async def test_episodic_records_protocol_error_for_empty_tool_name() -> None:
    """A tool_use missing `name` produces an episodic record under '<unknown>'."""
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "", {"action": "noop"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("done")], stop_reason="end_turn"),
        ]
    )
    coll = _make_collection(_FakeTool(ToolResult(output="ok")))
    episodic = _RecordingEpisodic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        episodic=episodic,
    )

    assert len(episodic.calls) == 1
    rec = episodic.calls[0]["record"]
    assert rec["tool"] == "<unknown>"
    assert rec["is_error"] is True
    assert rec["error_code"] == ErrorCode.TOOL_INTERNAL_ERROR.value


@pytest.mark.asyncio
async def test_episodic_records_tool_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool that exceeds OPENMIMI_TOOL_TIMEOUT_S still emits one record."""
    monkeypatch.setenv("OPENMIMI_TOOL_TIMEOUT_S", "0.05")
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "wait"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("retry done")], stop_reason="end_turn"),
        ]
    )

    async def _slow(_inp: dict[str, Any]) -> ToolResult:
        await asyncio.sleep(5.0)
        return ToolResult(output="never")

    class _SlowTool(ToolBase):
        name = "browser"

        def to_params(self) -> dict[str, Any]:
            return {"name": self.name, "description": "slow", "input_schema": {}}

        async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
            return await _slow(tool_input)

    coll = _make_collection(_SlowTool())
    episodic = _RecordingEpisodic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        episodic=episodic,
    )

    assert len(episodic.calls) == 1
    rec = episodic.calls[0]["record"]
    assert rec["is_error"] is True
    assert rec["error_code"] == ErrorCode.TOOL_INTERNAL_ERROR.value
    assert "timed out" in rec["result_summary"].lower()


@pytest.mark.asyncio
async def test_episodic_failure_does_not_break_loop() -> None:
    """An exception inside append_step is swallowed; the loop keeps running."""
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("done")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)
    episodic = _RecordingEpisodic(behaviour=OSError("disk full"))

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await sampling_loop(
        messages=messages,
        tools=coll,
        llm=llm,
        session_id="s",
        episodic=episodic,
    )

    # Loop completed: 1 user + 2 assistant + 1 user(tool_results) = 4 messages.
    assert len(out) == 4
    # The append still happened (we recorded it before raising).
    assert len(episodic.calls) == 1


@pytest.mark.asyncio
async def test_no_episodic_means_no_calls() -> None:
    """The loop must not error when episodic is None (default)."""
    llm = _ScriptedLLM(
        [
            _assistant(
                [_tool_use("tu1", "browser", {"action": "screenshot"})],
                stop_reason="tool_use",
            ),
            _assistant([_text("done")], stop_reason="end_turn"),
        ]
    )
    tool = _FakeTool(ToolResult(output="ok"))
    coll = _make_collection(tool)

    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    # episodic kwarg omitted entirely.
    out = await sampling_loop(
        messages=messages, tools=coll, llm=llm, session_id="s"
    )
    assert len(out) == 4


# --- _strip_images_for_dump (#prompt-debug) ---------------------------------


def test_strip_images_for_dump_replaces_top_level_image() -> None:
    from openmimi.loop import _strip_images_for_dump

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "abcd1234" * 1000,
                    },
                },
            ],
        }
    ]
    out = _strip_images_for_dump(messages)
    blocks = out[0]["content"]
    assert blocks[0] == {"type": "text", "text": "hi"}
    assert blocks[1] == {"type": "text", "text": "[image stripped for debug dump]"}
    # Original untouched
    assert messages[0]["content"][1]["type"] == "image"


def test_strip_images_for_dump_replaces_image_inside_tool_result() -> None:
    from openmimi.loop import _strip_images_for_dump

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "content": [
                        {"type": "text", "text": "screenshot taken"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "xyz789" * 1000,
                            },
                        },
                    ],
                }
            ],
        }
    ]
    out = _strip_images_for_dump(messages)
    tr = out[0]["content"][0]
    sub = tr["content"]
    assert sub[0] == {"type": "text", "text": "screenshot taken"}
    assert sub[1] == {"type": "text", "text": "[image stripped for debug dump]"}


def test_strip_images_for_dump_leaves_text_and_tool_use_alone() -> None:
    from openmimi.loop import _strip_images_for_dump

    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "x", "name": "browser", "input": {}},
            ],
        }
    ]
    out = _strip_images_for_dump(messages)
    assert out == messages


def test_strip_images_for_dump_handles_string_content() -> None:
    from openmimi.loop import _strip_images_for_dump

    messages = [{"role": "user", "content": "plain text"}]
    out = _strip_images_for_dump(messages)
    assert out == messages
