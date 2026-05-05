"""Browser tool: wraps `browser-use` to provide Anthropic-style tool actions.

Adapter design intent: depend only on browser-use's public API so upstream
upgrades remain a one-line bump.
"""
from __future__ import annotations

from typing import Any

from .base import ToolBase
from .browser_schema import (
    BROWSER_TOOL_INPUT_ADAPTER,
    browser_tool_input_json_schema,
)
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Operate a Chromium browser with mixed locator strategies "
    "(target_text / target_hint / coordinate; coordinate is mutually exclusive "
    "with the semantic targets). Every call returns a fresh screenshot plus the "
    "current URL/title in `details`. Prefer `target_text`/`target_hint`; fall "
    "back to `coordinate` only when the element has no stable text."
)


class BrowserTool(ToolBase):
    name = "browser"

    def __init__(
        self,
        *,
        download_dir: str,
        viewport: tuple[int, int] = (1280, 800),
    ) -> None:
        self._download_dir = download_dir
        self._viewport = viewport
        self._session: Any = None

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": _TOOL_DESCRIPTION,
            "input_schema": browser_tool_input_json_schema(),
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        validated = BROWSER_TOOL_INPUT_ADAPTER.validate_python(tool_input)
        # M1: dispatch by action type; concrete handlers wrap browser-use here.
        raise NotImplementedError(
            f"M1: implement handler for action={validated.action!r}"
        )

    async def close(self) -> None:
        if self._session is not None:
            self._session = None
