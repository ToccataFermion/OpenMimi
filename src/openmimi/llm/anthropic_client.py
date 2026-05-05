"""Anthropic implementation of LLMClient (tool_use / tool_result).

Wraps `anthropic.AsyncAnthropic.messages.create` and exposes the same
provider-agnostic shape that `sampling_loop` consumes (a plain dict that
looks like an Anthropic Message: `content`, `stop_reason`, `usage`, ...).

Prompt caching is opt-in (default on) and applied in the conservative,
high-leverage spots:
- the system prompt block, and
- the last tool definition (which marks the entire tools array as cacheable).
"""
from __future__ import annotations

from typing import Any, Protocol


class _AsyncMessagesLike(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class _AsyncClientLike(Protocol):
    @property
    def messages(self) -> _AsyncMessagesLike: ...


class AnthropicClient:
    """LLMClient backed by anthropic.AsyncAnthropic.

    The optional `client` argument lets tests inject a stub that records calls
    and returns a canned Message-shaped object without touching the network.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str,
        enable_prompt_caching: bool = True,
        client: _AsyncClientLike | None = None,
    ) -> None:
        self._model = model
        self._enable_caching = enable_prompt_caching
        if client is not None:
            self._client = client
        else:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=api_key)

    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        system_param = self._build_system(system)
        tools_param = self._build_tools(tools)

        result = await self._client.messages.create(
            model=self._model,
            system=system_param,
            messages=messages,
            tools=tools_param,
            max_tokens=max_tokens,
        )
        return _to_dict(result)

    def _build_system(self, system: str) -> Any:
        if not self._enable_caching or not system:
            return system
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _build_tools(
        self, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not tools:
            return []
        cloned = [dict(t) for t in tools]
        if not self._enable_caching:
            return cloned
        cloned[-1] = {**cloned[-1], "cache_control": {"type": "ephemeral"}}
        return cloned


def _to_dict(message: Any) -> dict[str, Any]:
    """Coerce an Anthropic Message (or compatible stub) into a plain dict."""
    if isinstance(message, dict):
        return message
    if hasattr(message, "model_dump"):
        return message.model_dump()
    if hasattr(message, "dict"):
        return message.dict()
    return dict(message)


__all__ = ["AnthropicClient"]
