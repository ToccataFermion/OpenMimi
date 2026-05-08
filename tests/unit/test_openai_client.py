"""Unit tests for OpenAIChatClient mappings (no network)."""

from __future__ import annotations

from typing import Any

import pytest

from openmimi.llm import openai_client as oc


def test_tools_mapping() -> None:
    tools = [
        {
            "name": "agent_browser",
            "description": "Browser",
            "input_schema": {"type": "object", "properties": {"action": {"type": "string"}}},
        }
    ]
    mapped = oc._anthropic_tools_to_openai(tools)
    assert mapped[0]["type"] == "function"
    assert mapped[0]["function"]["name"] == "agent_browser"
    assert mapped[0]["function"]["parameters"]["type"] == "object"


def test_messages_mapping_tool_pair() -> None:
    system = "sys"
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "shell", "input": {"command": "echo x"}}],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": [{"type": "text", "text": "ok"}]}
            ],
        },
    ]
    out = oc._anthropic_messages_to_openai(system=system, messages=messages)
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[2]["role"] == "assistant" and out[2].get("tool_calls")
    assert out[3]["role"] == "tool" and out[3]["tool_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_create_invokes_chat_completions() -> None:
    captured: dict[str, Any] = {}

    class _FakeCompletions:
        async def create(self, **kwargs: Any) -> object:
            captured.update(kwargs)

            class _Msg:
                content = "ok"
                tool_calls = []

            class _Choice:
                finish_reason = "stop"
                message = _Msg()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    client = oc.OpenAIChatClient(api_key="x", model="m", client=_FakeClient())
    shaped = await client.create(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}],
        max_tokens=10,
    )
    assert captured["model"] == "m"
    assert captured["tool_choice"] == "auto"
    assert shaped["stop_reason"] == "end_turn"


def test_openai_response_maps_tool_call() -> None:
    class _Fn:
        name = "agent_browser"
        arguments = '{"action": "snapshot"}'

    class _Tc:
        id = "call_xyz"
        function = _Fn()

    class _Msg:
        content = None
        tool_calls = [_Tc()]

    class _Choice:
        finish_reason = "tool_calls"
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    shaped = oc._openai_completion_to_anthropic_shape(_Resp())
    assert shaped["stop_reason"] == "tool_use"
    tool_use = [b for b in shaped["content"] if b.get("type") == "tool_use"][0]
    assert tool_use["id"] == "call_xyz"
    assert tool_use["input"] == {"action": "snapshot"}

