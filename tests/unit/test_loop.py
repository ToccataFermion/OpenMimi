"""Unit tests for the sampling loop.

The tests use fake LLM / tool / audit doubles to keep the loop fully
deterministic and offline.
"""
from __future__ import annotations

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
