"""Unit tests for AnthropicClient (no network calls).

The tests inject a stub client into AnthropicClient via the `client=`
constructor argument so we can record calls and return canned Message-like
objects without touching the Anthropic API.
"""
from __future__ import annotations

from typing import Any

import pytest

from openmimi.llm.anthropic_client import AnthropicClient


class _FakeMessages:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response: Any) -> None:
        self.messages = _FakeMessages(response)


class _FakeMessageObject:
    """Mimics anthropic Message: has model_dump()."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return dict(self._payload)


@pytest.mark.asyncio
async def test_create_returns_dict_from_message_object() -> None:
    payload = {
        "id": "msg_1",
        "role": "assistant",
        "content": [{"type": "text", "text": "hi"}],
        "stop_reason": "end_turn",
    }
    fake = _FakeClient(_FakeMessageObject(payload))
    client = AnthropicClient(model="claude-x", client=fake)

    out = await client.create(
        system="you are useful",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        max_tokens=128,
    )

    assert out == payload
    assert len(fake.messages.calls) == 1
    call = fake.messages.calls[0]
    assert call["model"] == "claude-x"
    assert call["max_tokens"] == 128


@pytest.mark.asyncio
async def test_caching_wraps_system_into_text_block() -> None:
    fake = _FakeClient(_FakeMessageObject({"content": [], "stop_reason": "end_turn"}))
    client = AnthropicClient(model="claude-x", client=fake)

    await client.create(
        system="ROLE", messages=[], tools=[], max_tokens=8
    )

    sys_param = fake.messages.calls[0]["system"]
    assert isinstance(sys_param, list)
    assert len(sys_param) == 1
    assert sys_param[0]["type"] == "text"
    assert sys_param[0]["text"] == "ROLE"
    assert sys_param[0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_caching_disabled_keeps_plain_strings() -> None:
    fake = _FakeClient(_FakeMessageObject({"content": [], "stop_reason": "end_turn"}))
    client = AnthropicClient(
        model="claude-x", client=fake, enable_prompt_caching=False
    )

    await client.create(
        system="ROLE",
        messages=[],
        tools=[{"name": "browser", "description": "x", "input_schema": {}}],
        max_tokens=8,
    )

    sys_param = fake.messages.calls[0]["system"]
    assert sys_param == "ROLE"
    tools_param = fake.messages.calls[0]["tools"]
    assert "cache_control" not in tools_param[0]


@pytest.mark.asyncio
async def test_caching_marks_only_last_tool() -> None:
    fake = _FakeClient(_FakeMessageObject({"content": [], "stop_reason": "end_turn"}))
    client = AnthropicClient(model="claude-x", client=fake)

    await client.create(
        system="r",
        messages=[],
        tools=[
            {"name": "a", "description": "x", "input_schema": {}},
            {"name": "b", "description": "y", "input_schema": {}},
        ],
        max_tokens=8,
    )

    tools_param = fake.messages.calls[0]["tools"]
    assert "cache_control" not in tools_param[0]
    assert tools_param[1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_create_tolerates_dict_response() -> None:
    raw = {"role": "assistant", "content": [], "stop_reason": "end_turn"}
    fake = _FakeClient(raw)
    client = AnthropicClient(model="claude-x", client=fake)

    out = await client.create(system="r", messages=[], tools=[], max_tokens=8)
    assert out == raw


@pytest.mark.asyncio
async def test_progress_logger_called_before_and_after() -> None:
    fake = _FakeClient(
        _FakeMessageObject({"content": [], "stop_reason": "tool_use"})
    )
    log: list[str] = []
    client = AnthropicClient(
        model="claude-x", client=fake, progress_logger=log.append
    )

    await client.create(system="r", messages=[], tools=[], max_tokens=8)
    await client.create(system="r", messages=[], tools=[], max_tokens=8)

    assert len(log) == 4
    assert log[0].startswith("[llm] turn 1: requesting")
    assert "timeout=" in log[0]
    assert log[1].startswith("[llm] turn 1: response in")
    assert "stop=tool_use" in log[1]
    assert log[2].startswith("[llm] turn 2: requesting")
    assert log[3].startswith("[llm] turn 2: response in")


@pytest.mark.asyncio
async def test_progress_logger_called_on_failure_and_reraises() -> None:
    class _BoomMessages:
        async def create(self, **kwargs: Any) -> Any:
            raise RuntimeError("upstream timed out")

    class _BoomClient:
        def __init__(self) -> None:
            self.messages = _BoomMessages()

    log: list[str] = []
    client = AnthropicClient(
        model="claude-x", client=_BoomClient(), progress_logger=log.append
    )

    with pytest.raises(RuntimeError):
        await client.create(system="r", messages=[], tools=[], max_tokens=8)

    assert len(log) == 2
    assert log[0].startswith("[llm] turn 1: requesting")
    assert log[1].startswith("[llm] turn 1: failed after")
    assert "RuntimeError" in log[1]
    assert "upstream timed out" in log[1]


@pytest.mark.asyncio
async def test_progress_logger_exception_does_not_break_create() -> None:
    fake = _FakeClient(
        _FakeMessageObject({"content": [], "stop_reason": "end_turn"})
    )

    def _broken(_: str) -> None:
        raise RuntimeError("logger died")

    client = AnthropicClient(
        model="claude-x", client=fake, progress_logger=_broken
    )

    out = await client.create(system="r", messages=[], tools=[], max_tokens=8)
    assert out["stop_reason"] == "end_turn"


def test_real_client_construction_passes_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAsyncAnthropic)

    AnthropicClient(api_key="k", model="m", request_timeout_s=42.5)
    assert captured["api_key"] == "k"
    assert captured["timeout"] == 42.5
    assert "base_url" not in captured

    captured.clear()
    AnthropicClient(
        api_key="k",
        model="m",
        base_url="https://x.example.com",
        request_timeout_s=7.0,
    )
    assert captured["base_url"] == "https://x.example.com"
    assert captured["timeout"] == 7.0
