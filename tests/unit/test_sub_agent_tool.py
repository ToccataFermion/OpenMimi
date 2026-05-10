"""Tests for SubAgentTool (roadmap #8 stage 3)."""
from __future__ import annotations

from typing import Any

import pytest

from openmimi.sub_agent import SubAgentRequest, SubAgentResult
from openmimi.tools.errors import ErrorCode
from openmimi.tools.result import ToolResult
from openmimi.tools.sub_agent_tool import SubAgentTool


# ---------------------------------------------------------------------------
# Fake runner — records requests, returns scripted results.
# ---------------------------------------------------------------------------


class _FakeRunner:
    """Stand-in for ``SubAgentRunner`` that returns a fixed result."""

    def __init__(self, result: SubAgentResult | None = None) -> None:
        self.result = result or SubAgentResult(
            text="ok",
            turns=1,
            sub_session_id="P--sub-0",
            is_error=False,
        )
        self.requests: list[SubAgentRequest] = []

    async def run(self, request: SubAgentRequest) -> SubAgentResult:
        self.requests.append(request)
        return self.result


# ---------------------------------------------------------------------------
# to_params
# ---------------------------------------------------------------------------


def test_to_params_basic_shape() -> None:
    tool = SubAgentTool(_FakeRunner())  # type: ignore[arg-type]
    params = tool.to_params()
    assert params["name"] == "sub_agent"
    schema = params["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["task"]
    props = schema["properties"]
    assert set(props) >= {"task", "allowed_tools", "max_turns", "system_addendum"}
    assert props["task"]["type"] == "string"
    assert props["allowed_tools"]["type"] == "array"
    assert props["allowed_tools"]["items"]["type"] == "string"
    assert props["max_turns"]["type"] == "integer"
    assert props["system_addendum"]["type"] == "string"


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_forwards_to_runner() -> None:
    runner = _FakeRunner(
        SubAgentResult(
            text="42",
            turns=2,
            sub_session_id="P--sub-0",
            is_error=False,
        )
    )
    tool = SubAgentTool(
        runner,  # type: ignore[arg-type]
        session_provider=lambda: "P",
    )
    res = await tool({"task": "solve x"})
    assert isinstance(res, ToolResult)
    assert res.output == "42"
    assert res.is_error is False
    assert res.details == {"sub_session_id": "P--sub-0", "turns": 2}
    assert res.structured == {
        "turns": 2,
        "sub_session_id": "P--sub-0",
        "is_error": False,
    }
    # Runner saw exactly one request with the expected payload.
    assert len(runner.requests) == 1
    req = runner.requests[0]
    assert req.task == "solve x"
    assert req.allowed_tools is None
    assert req.max_turns == 10  # default
    assert req.system_addendum == ""
    assert req.parent_session_id == "P"
    assert req.sub_index == 0


@pytest.mark.asyncio
async def test_passes_through_optional_fields() -> None:
    runner = _FakeRunner()
    tool = SubAgentTool(runner, session_provider=lambda: "PSID")  # type: ignore[arg-type]
    await tool(
        {
            "task": "do x",
            "allowed_tools": ["alpha", "beta"],
            "max_turns": 3,
            "system_addendum": "Reply in English.",
        }
    )
    req = runner.requests[0]
    assert req.allowed_tools == ["alpha", "beta"]
    assert req.max_turns == 3
    assert req.system_addendum == "Reply in English."
    assert req.parent_session_id == "PSID"


@pytest.mark.asyncio
async def test_allowed_tools_filters_non_strings_silently() -> None:
    runner = _FakeRunner()
    tool = SubAgentTool(runner)  # type: ignore[arg-type]
    await tool({"task": "x", "allowed_tools": ["a", "", None, 42, "b"]})
    req = runner.requests[0]
    assert req.allowed_tools == ["a", "b"]


@pytest.mark.asyncio
async def test_allowed_tools_empty_list_preserved() -> None:
    """[] explicitly disables all tools — must reach runner as []."""
    runner = _FakeRunner()
    tool = SubAgentTool(runner)  # type: ignore[arg-type]
    await tool({"task": "x", "allowed_tools": []})
    req = runner.requests[0]
    assert req.allowed_tools == []


# ---------------------------------------------------------------------------
# Counter / session provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counter_is_monotonic() -> None:
    runner = _FakeRunner()
    tool = SubAgentTool(runner, session_provider=lambda: "P")  # type: ignore[arg-type]
    for _ in range(3):
        await tool({"task": "x"})
    indices = [r.sub_index for r in runner.requests]
    assert indices == [0, 1, 2]


@pytest.mark.asyncio
async def test_session_provider_is_consulted_each_call() -> None:
    """Provider returns dynamic values — tool re-reads on every call."""
    holder = {"sid": "first"}
    runner = _FakeRunner()
    tool = SubAgentTool(
        runner,  # type: ignore[arg-type]
        session_provider=lambda: holder["sid"],
    )
    await tool({"task": "x"})
    holder["sid"] = "second"
    await tool({"task": "x"})
    assert runner.requests[0].parent_session_id == "first"
    assert runner.requests[1].parent_session_id == "second"


@pytest.mark.asyncio
async def test_session_provider_none_yields_empty_parent_id() -> None:
    runner = _FakeRunner()
    tool = SubAgentTool(runner, session_provider=None)  # type: ignore[arg-type]
    await tool({"task": "x"})
    assert runner.requests[0].parent_session_id == ""


@pytest.mark.asyncio
async def test_session_provider_exception_yields_empty_parent_id() -> None:
    def _broken() -> str:
        raise RuntimeError("oops")

    runner = _FakeRunner()
    tool = SubAgentTool(runner, session_provider=_broken)  # type: ignore[arg-type]
    await tool({"task": "x"})
    assert runner.requests[0].parent_session_id == ""


@pytest.mark.asyncio
async def test_set_session_provider_rebinds_after_construction() -> None:
    runner = _FakeRunner()
    tool = SubAgentTool(runner)  # type: ignore[arg-type]
    await tool({"task": "x"})
    assert runner.requests[0].parent_session_id == ""
    tool.set_session_provider(lambda: "LATE")
    await tool({"task": "x"})
    assert runner.requests[1].parent_session_id == "LATE"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_task_is_invalid_input() -> None:
    tool = SubAgentTool(_FakeRunner())  # type: ignore[arg-type]
    res = await tool({})
    assert res.is_error is True
    assert res.details.get("error_code") == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_empty_task_is_invalid_input() -> None:
    tool = SubAgentTool(_FakeRunner())  # type: ignore[arg-type]
    res = await tool({"task": ""})
    assert res.is_error is True
    assert res.details.get("error_code") == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_whitespace_task_is_invalid_input() -> None:
    tool = SubAgentTool(_FakeRunner())  # type: ignore[arg-type]
    res = await tool({"task": "   \n  "})
    assert res.is_error is True
    assert res.details.get("error_code") == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_non_string_task_is_invalid_input() -> None:
    tool = SubAgentTool(_FakeRunner())  # type: ignore[arg-type]
    res = await tool({"task": 42})
    assert res.is_error is True
    assert res.details.get("error_code") == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_allowed_tools_must_be_list() -> None:
    tool = SubAgentTool(_FakeRunner())  # type: ignore[arg-type]
    res = await tool({"task": "x", "allowed_tools": "alpha,beta"})
    assert res.is_error is True
    assert res.details.get("error_code") == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_max_turns_must_be_int() -> None:
    tool = SubAgentTool(_FakeRunner())  # type: ignore[arg-type]
    res = await tool({"task": "x", "max_turns": "ten"})
    assert res.is_error is True
    assert res.details.get("error_code") == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_max_turns_rejects_bool() -> None:
    """``bool`` is a subclass of ``int`` in Python — explicitly reject it."""
    tool = SubAgentTool(_FakeRunner())  # type: ignore[arg-type]
    res = await tool({"task": "x", "max_turns": True})
    assert res.is_error is True
    assert res.details.get("error_code") == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_max_turns_zero_passed_through() -> None:
    """0 reaches the runner; SubAgentRunner normalizes to 1 there."""
    runner = _FakeRunner()
    tool = SubAgentTool(runner)  # type: ignore[arg-type]
    await tool({"task": "x", "max_turns": 0})
    assert runner.requests[0].max_turns == 0


@pytest.mark.asyncio
async def test_system_addendum_coerced_to_string() -> None:
    """Non-string addenda are stringified rather than rejected."""
    runner = _FakeRunner()
    tool = SubAgentTool(runner)  # type: ignore[arg-type]
    await tool({"task": "x", "system_addendum": 12345})
    assert runner.requests[0].system_addendum == "12345"


# ---------------------------------------------------------------------------
# Error result mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_error_propagates_to_tool_result() -> None:
    runner = _FakeRunner(
        SubAgentResult(
            text="(sub-agent X failed: ...)",
            turns=0,
            sub_session_id="P--sub-0",
            is_error=True,
            error_message="boom",
        )
    )
    tool = SubAgentTool(runner)  # type: ignore[arg-type]
    res = await tool({"task": "x"})
    assert res.is_error is True
    assert res.output.startswith("(sub-agent")
    assert res.structured is not None
    assert res.structured["is_error"] is True
    assert res.structured["error_message"] == "boom"
    assert res.structured["sub_session_id"] == "P--sub-0"
    assert res.structured["turns"] == 0


@pytest.mark.asyncio
async def test_structured_omits_error_message_on_success() -> None:
    runner = _FakeRunner(
        SubAgentResult(
            text="ok",
            turns=1,
            sub_session_id="P--sub-0",
            is_error=False,
            error_message=None,
        )
    )
    tool = SubAgentTool(runner)  # type: ignore[arg-type]
    res = await tool({"task": "x"})
    assert res.structured is not None
    assert "error_message" not in res.structured
    assert res.structured["is_error"] is False


# ---------------------------------------------------------------------------
# Tool registration sanity (catches typos in tool name / schema)
# ---------------------------------------------------------------------------


def test_tool_name_is_sub_agent() -> None:
    tool = SubAgentTool(_FakeRunner())  # type: ignore[arg-type]
    assert tool.name == "sub_agent"
    assert tool.to_params()["name"] == "sub_agent"


def test_tool_input_schema_has_no_unexpected_required() -> None:
    """Only 'task' should be required; everything else optional."""
    tool = SubAgentTool(_FakeRunner())  # type: ignore[arg-type]
    schema = tool.to_params()["input_schema"]
    assert schema["required"] == ["task"]
