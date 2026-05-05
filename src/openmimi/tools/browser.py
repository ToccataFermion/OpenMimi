"""Browser tool: wraps `browser-use` to provide Anthropic-style tool actions.

Adapter design intent: depend only on browser-use's public API so upstream
upgrades remain a one-line bump.
"""
from __future__ import annotations

from typing import Any

from .base import ToolBase
from .result import ToolResult


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
        # Final input_schema is defined in the next task (Pydantic model).
        return {
            "name": self.name,
            "description": (
                "Operate a Chromium browser with mixed locator strategies "
                "(target_text / target_hint / coordinate). Each call returns a "
                "fresh screenshot and the current URL."
            ),
            "input_schema": {"type": "object"},
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        raise NotImplementedError(
            "M1: implement actions navigate/click/type/press/scroll/wait/screenshot/extract/download"
        )

    async def close(self) -> None:
        if self._session is not None:
            self._session = None
