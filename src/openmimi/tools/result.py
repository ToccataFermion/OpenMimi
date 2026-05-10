"""ToolResult: unified return shape for all tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    output: str = ""
    base64_image: str | None = None
    is_error: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    # Optional raw data payload for programmatic consumers (planner / sub-agents).
    # The LLM still reads ``output`` (text); ``structured`` lets callers skip
    # re-parsing JSON they already constructed in the handler. Distinct from
    # ``details``, which carries metadata (error codes, hints) about the result.
    structured: dict[str, Any] | None = None
