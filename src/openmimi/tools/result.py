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
