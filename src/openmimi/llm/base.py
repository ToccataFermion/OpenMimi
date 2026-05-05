"""LLMClient protocol."""
from __future__ import annotations

from typing import Any, Protocol


class LLMClient(Protocol):
    """Provider-agnostic LLM interface used by the sampling loop."""

    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> dict[str, Any]: ...
