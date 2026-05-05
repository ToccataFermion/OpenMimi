"""Tool registry and dispatcher."""
from __future__ import annotations

from typing import Any

from .base import ToolBase
from .result import ToolResult


class ToolCollection:
    def __init__(self) -> None:
        self._tools: dict[str, ToolBase] = {}

    def register(self, tool: ToolBase) -> None:
        self._tools[tool.name] = tool

    def to_params(self) -> list[dict[str, Any]]:
        return [t.to_params() for t in self._tools.values()]

    async def run(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return await self._tools[name](tool_input)

    async def close_all(self) -> None:
        for tool in self._tools.values():
            await tool.close()
