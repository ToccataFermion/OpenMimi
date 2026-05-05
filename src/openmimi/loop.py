"""Sampling loop: LLM -> tool_use -> tools.run -> tool_result -> repeat."""
from __future__ import annotations

from typing import Any


async def sampling_loop(
    *,
    messages: list[dict[str, Any]],
    tools: Any,
    llm: Any,
    audit: Any,
    max_turns: int = 30,
) -> list[dict[str, Any]]:
    """LLM-driven tool_use loop. Returns the final messages list.

    Mirrors the structure of `anthropic-quickstarts/computer-use-demo/loop.py`
    but with OpenMimi's ToolCollection / LLMClient / audit interfaces.
    """
    raise NotImplementedError("M1: implement sampling loop")
