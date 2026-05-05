"""ToolBase abstract class."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .result import ToolResult


class ToolBase(ABC):
    """All OpenMimi tools implement this contract."""

    name: str

    @abstractmethod
    def to_params(self) -> dict[str, Any]:
        """Schema dict for the LLM (Anthropic-style tool definition)."""

    @abstractmethod
    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        """Execute the tool and return a unified ToolResult."""

    async def close(self) -> None:
        return None
