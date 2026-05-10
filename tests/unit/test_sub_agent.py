"""Tests for sub-agent isolation (roadmap #8 stages 1-2)."""
from __future__ import annotations

from typing import Any

import pytest

from openmimi.sub_agent import (
    SubAgentRequest,
    SubAgentResult,
    SubAgentRunner,
    _build_sub_system_prompt,
    _count_assistant_turns,
    _final_assistant_text,
    default_sub_session_id,
    filter_tools,
)
from openmimi.tools.base import ToolBase
from openmimi.tools.collection import ToolCollection
from openmimi.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


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
                "tools": [dict(t) for t in tools],
                "max_tokens": max_tokens,
            }
        )
        if not self._responses:
            raise AssertionError("ScriptedLLM ran out of responses")
        return self._responses.pop(0)


class _NoopTool(ToolBase):
    """Minimal ToolBase impl that does nothing useful."""

    def __init__(self, name: str = "noop") -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "noop test tool",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        self.calls.append(tool_input)
        return ToolResult(output="ok", details={"echo": tool_input})


class _RecordingEpisodic:
    def __init__(self) -> None:
        self.appends: list[dict[str, Any]] = []

    def append_step(
        self, *, session_id: str, step: int, record: dict[str, Any]
    ) -> None:
        self.appends.append(
            {"session_id": session_id, "step": step, "record": dict(record)}
        )


def _text_reply(text: str) -> dict[str, Any]:
    return {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
    }


def _tool_use_reply(tool_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "stop_reason": "tool_use",
        "content": [
            {"type": "tool_use", "id": tool_id, "name": name, "input": payload}
        ],
    }


# ---------------------------------------------------------------------------
# default_sub_session_id
# ---------------------------------------------------------------------------


def test_default_sub_session_id_with_parent() -> None:
    assert default_sub_session_id("sess-abc", 0) == "sess-abc--sub-0"
    assert default_sub_session_id("sess-abc", 7) == "sess-abc--sub-7"


def test_default_sub_session_id_without_parent() -> None:
    assert default_sub_session_id("", 3) == "sub-3"
    assert default_sub_session_id("   ", 1) == "sub-1"


# ---------------------------------------------------------------------------
# filter_tools
# ---------------------------------------------------------------------------


def _parent_with(*names: str) -> ToolCollection:
    coll = ToolCollection()
    for n in names:
        coll.register(_NoopTool(n))
    return coll


def test_filter_tools_none_means_all() -> None:
    parent = _parent_with("a", "b", "c")
    sub = filter_tools(parent, None)
    assert sorted(sub.names()) == ["a", "b", "c"]


def test_filter_tools_empty_list_disables_all() -> None:
    parent = _parent_with("a", "b")
    sub = filter_tools(parent, [])
    assert sub.names() == []


def test_filter_tools_subset() -> None:
    parent = _parent_with("a", "b", "c")
    sub = filter_tools(parent, ["a", "c"])
    assert sorted(sub.names()) == ["a", "c"]


def test_filter_tools_skips_unknown_names() -> None:
    parent = _parent_with("a", "b")
    sub = filter_tools(parent, ["a", "missing", "b"])
    assert sorted(sub.names()) == ["a", "b"]


def test_filter_tools_dedups() -> None:
    parent = _parent_with("a", "b")
    sub = filter_tools(parent, ["a", "a", "b", "a"])
    assert sorted(sub.names()) == ["a", "b"]


def test_filter_tools_shares_instances() -> None:
    """Sub-agent must share tool instances so daemon state is preserved."""
    parent = _parent_with("a")
    sub = filter_tools(parent, ["a"])
    assert parent.get("a") is sub.get("a")


def test_filter_tools_ignores_non_strings() -> None:
    parent = _parent_with("a")
    sub = filter_tools(parent, ["a", "", None, 42])  # type: ignore[list-item]
    assert sub.names() == ["a"]


def test_filter_tools_does_not_mutate_parent() -> None:
    parent = _parent_with("a", "b")
    filter_tools(parent, ["a"])
    assert sorted(parent.names()) == ["a", "b"]


# ---------------------------------------------------------------------------
# ToolCollection accessors
# ---------------------------------------------------------------------------


def test_tool_collection_names_and_get_and_contains() -> None:
    coll = _parent_with("x", "y")
    assert sorted(coll.names()) == ["x", "y"]
    assert coll.get("x") is not None and coll.get("x").name == "x"
    assert coll.get("missing") is None
    assert "x" in coll
    assert "missing" not in coll


# ---------------------------------------------------------------------------
# _build_sub_system_prompt
# ---------------------------------------------------------------------------


def test_build_sub_system_prompt_no_addendum() -> None:
    out = _build_sub_system_prompt("")
    assert "focused sub-agent" in out
    assert "Rules:" in out


def test_build_sub_system_prompt_with_addendum() -> None:
    out = _build_sub_system_prompt("Reply in English.")
    assert "focused sub-agent" in out
    assert out.endswith("Reply in English.")


def test_build_sub_system_prompt_strips_whitespace() -> None:
    out = _build_sub_system_prompt("   \n   ")
    # whitespace-only addendum should not add a separator
    assert out == _build_sub_system_prompt("")


# ---------------------------------------------------------------------------
# _final_assistant_text + _count_assistant_turns
# ---------------------------------------------------------------------------


def test_final_assistant_text_from_string_content() -> None:
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert _final_assistant_text(msgs) == "hello"


def test_final_assistant_text_from_blocks() -> None:
    msgs = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "tool_use", "id": "x", "name": "n", "input": {}},
                {"type": "text", "text": "second"},
            ],
        },
    ]
    assert _final_assistant_text(msgs) == "first\nsecond"


def test_final_assistant_text_skips_tool_results() -> None:
    msgs = [
        {"role": "assistant", "content": "earlier"},
        {"role": "user", "content": [{"type": "tool_result"}]},
    ]
    assert _final_assistant_text(msgs) == "earlier"


def test_final_assistant_text_returns_empty_if_no_text() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "x", "name": "n", "input": {}},
            ],
        },
    ]
    assert _final_assistant_text(msgs) == ""


def test_count_assistant_turns() -> None:
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "assistant", "content": "a3"},
    ]
    assert _count_assistant_turns(msgs) == 3


# ---------------------------------------------------------------------------
# SubAgentRequest / SubAgentResult dataclass defaults
# ---------------------------------------------------------------------------


def test_sub_agent_request_defaults() -> None:
    req = SubAgentRequest(task="do x")
    assert req.task == "do x"
    assert req.allowed_tools is None
    assert req.max_turns == 10
    assert req.system_addendum == ""
    assert req.parent_session_id == ""
    assert req.sub_index == 0


def test_sub_agent_result_defaults() -> None:
    res = SubAgentResult(text="ok")
    assert res.text == "ok"
    assert res.turns == 0
    assert res.sub_session_id == ""
    assert res.is_error is False
    assert res.error_message is None
    assert res.messages == []


# ---------------------------------------------------------------------------
# SubAgentRunner.run — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_happy_path_single_reply() -> None:
    """LLM replies with text immediately → SubAgentResult.text == that text."""
    llm = _ScriptedLLM([_text_reply("the answer is 42")])
    parent = _parent_with("noop")
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    result = await runner.run(
        SubAgentRequest(
            task="solve x",
            parent_session_id="sess-1",
            sub_index=0,
        )
    )
    assert result.is_error is False
    assert result.text == "the answer is 42"
    assert result.sub_session_id == "sess-1--sub-0"
    assert result.turns == 1
    assert result.error_message is None
    # The sub-agent should have seen the sub-agent system prompt
    assert "focused sub-agent" in llm.calls[0]["system"]


@pytest.mark.asyncio
async def test_runner_one_tool_then_reply() -> None:
    """tool_use → tool_result → text reply."""
    llm = _ScriptedLLM(
        [
            _tool_use_reply("call_1", "noop", {"q": "x"}),
            _text_reply("done after using noop"),
        ]
    )
    parent = _parent_with("noop")
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    result = await runner.run(
        SubAgentRequest(task="please run noop", sub_index=2, parent_session_id="P")
    )
    assert result.is_error is False
    assert result.text == "done after using noop"
    assert result.sub_session_id == "P--sub-2"
    # Parent's noop tool should have been invoked once.
    noop = parent.get("noop")
    assert noop is not None and len(noop.calls) == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_runner_addendum_appended_to_system_prompt() -> None:
    llm = _ScriptedLLM([_text_reply("ok")])
    parent = _parent_with("noop")
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    await runner.run(
        SubAgentRequest(
            task="x",
            system_addendum="Reply in English only.",
            parent_session_id="P",
        )
    )
    assert llm.calls[0]["system"].rstrip().endswith("Reply in English only.")


@pytest.mark.asyncio
async def test_runner_allowed_tools_filters_subset() -> None:
    """allowed_tools narrows the LLM's view to that subset."""
    llm = _ScriptedLLM([_text_reply("ok")])
    parent = _parent_with("alpha", "beta", "gamma")
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    await runner.run(
        SubAgentRequest(
            task="x",
            allowed_tools=["alpha", "gamma"],
        )
    )
    names = sorted(t["name"] for t in llm.calls[0]["tools"])
    assert names == ["alpha", "gamma"]


@pytest.mark.asyncio
async def test_runner_empty_allowed_tools_means_no_tools() -> None:
    llm = _ScriptedLLM([_text_reply("ok")])
    parent = _parent_with("a", "b")
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    await runner.run(SubAgentRequest(task="x", allowed_tools=[]))
    assert llm.calls[0]["tools"] == []


@pytest.mark.asyncio
async def test_runner_none_allowed_tools_inherits_all() -> None:
    llm = _ScriptedLLM([_text_reply("ok")])
    parent = _parent_with("a", "b")
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    await runner.run(SubAgentRequest(task="x", allowed_tools=None))
    names = sorted(t["name"] for t in llm.calls[0]["tools"])
    assert names == ["a", "b"]


# ---------------------------------------------------------------------------
# SubAgentRunner.run — failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_empty_task_is_error() -> None:
    llm = _ScriptedLLM([])  # never called
    parent = _parent_with("noop")
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    result = await runner.run(
        SubAgentRequest(task="", parent_session_id="P", sub_index=1)
    )
    assert result.is_error is True
    assert "empty task" in (result.error_message or "")
    assert result.sub_session_id == "P--sub-1"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_runner_whitespace_task_is_error() -> None:
    llm = _ScriptedLLM([])
    parent = _parent_with()
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    result = await runner.run(SubAgentRequest(task="   \n  "))
    assert result.is_error is True


@pytest.mark.asyncio
async def test_runner_llm_exception_becomes_error_result() -> None:
    class _BrokenLLM:
        async def create(self, **_kw: Any) -> dict[str, Any]:
            raise RuntimeError("upstream 502")

    parent = _parent_with("noop")
    runner = SubAgentRunner(llm=_BrokenLLM(), parent_tools=parent)  # type: ignore[arg-type]
    result = await runner.run(SubAgentRequest(task="x", parent_session_id="P"))
    assert result.is_error is True
    assert "RuntimeError" in (result.error_message or "")
    assert "upstream 502" in result.text
    assert result.sub_session_id == "P--sub-0"


@pytest.mark.asyncio
async def test_runner_no_final_text_is_error() -> None:
    """If the loop hits max_turns without ending on plain text, flag as error."""
    # max_turns=1 → after first tool_use the loop returns even though the
    # assistant never produced a text-only reply.
    llm = _ScriptedLLM([_tool_use_reply("c1", "noop", {})])
    parent = _parent_with("noop")
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    result = await runner.run(SubAgentRequest(task="x", max_turns=1))
    assert result.is_error is True
    assert "no final text" in (result.error_message or "")


@pytest.mark.asyncio
async def test_runner_max_turns_normalized_to_at_least_one() -> None:
    """A request with max_turns=0 should still try one turn, not skip the LLM."""
    llm = _ScriptedLLM([_text_reply("ok")])
    parent = _parent_with()
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    result = await runner.run(SubAgentRequest(task="x", max_turns=0))
    assert result.is_error is False
    assert result.text == "ok"


# ---------------------------------------------------------------------------
# Episodic sink isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_episodic_uses_sub_session_id() -> None:
    """Episodic appends from the sub-agent must use the sub-session id."""
    llm = _ScriptedLLM(
        [
            _tool_use_reply("c1", "noop", {"q": "x"}),
            _text_reply("done"),
        ]
    )
    parent = _parent_with("noop")
    sink = _RecordingEpisodic()
    runner = SubAgentRunner(
        llm=llm, parent_tools=parent, episodic=sink
    )
    result = await runner.run(
        SubAgentRequest(task="x", parent_session_id="P", sub_index=3)
    )
    assert result.is_error is False
    assert sink.appends, "expected at least one episodic append"
    for entry in sink.appends:
        assert entry["session_id"] == "P--sub-3"


@pytest.mark.asyncio
async def test_runner_no_episodic_does_not_break() -> None:
    """Episodic-less sub-agent works the same as with one."""
    llm = _ScriptedLLM([_text_reply("ok")])
    parent = _parent_with()
    runner = SubAgentRunner(llm=llm, parent_tools=parent, episodic=None)
    result = await runner.run(SubAgentRequest(task="x"))
    assert result.is_error is False
    assert result.text == "ok"


# ---------------------------------------------------------------------------
# Parent state safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_does_not_mutate_parent_tools() -> None:
    llm = _ScriptedLLM([_text_reply("ok")])
    parent = _parent_with("a", "b", "c")
    before = sorted(parent.names())
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    await runner.run(SubAgentRequest(task="x", allowed_tools=["a"]))
    assert sorted(parent.names()) == before


@pytest.mark.asyncio
async def test_runner_subagent_results_do_not_leak_into_parent_messages() -> None:
    """The parent's messages list is independent from the sub-agent's."""
    llm = _ScriptedLLM([_text_reply("ok")])
    parent = _parent_with()
    runner = SubAgentRunner(llm=llm, parent_tools=parent)
    result = await runner.run(SubAgentRequest(task="x"))
    # The result's messages list is the sub-agent's own, not the parent's.
    assert isinstance(result.messages, list)
    assert result.messages[0]["content"] == "x"
    # And the parent doesn't carry it.
    assert "x" not in parent.names()
