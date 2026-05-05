"""Anthropic implementation of LLMClient (tool_use / tool_result)."""
from __future__ import annotations

from typing import Any


class AnthropicClient:
    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        raise NotImplementedError("M1: integrate anthropic SDK with prompt caching")
