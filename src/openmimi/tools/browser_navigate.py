"""Browser navigation facade: exposes navigation, tab, and viewport actions."""
from __future__ import annotations

from typing import Any

from .base import ToolBase
from .result import ToolResult


class BrowserNavigateTool(ToolBase):
    """Thin facade over AgentBrowserTool — only navigation actions."""

    name = "browser_navigate"

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Navigate the browser and manage tabs/viewport. "
                "Actions: navigate (load URL), back, forward, reload, "
                "tab_list, tab_switch, tab_new, tab_close, "
                "wait_for_navigation, set_viewport, emulate_device, focus."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "navigate",
                            "back",
                            "forward",
                            "reload",
                            "tab_list",
                            "tab_switch",
                            "tab_new",
                            "tab_close",
                            "wait_for_navigation",
                            "set_viewport",
                            "emulate_device",
                            "focus",
                        ],
                        "description": "Navigation or browser-state action.",
                    },
                    "url": {"type": "string", "description": "URL for navigate action."},
                    "tab_index": {
                        "type": "integer",
                        "description": "1-based tab index for tab_switch / tab_close.",
                    },
                    "width": {"type": "integer", "description": "Viewport width for set_viewport."},
                    "height": {"type": "integer", "description": "Viewport height for set_viewport."},
                    "device_name": {
                        "type": "string",
                        "description": "Device name for emulate_device (iPhone 14, Pixel 7, iPad Mini, reset).",
                    },
                    "milliseconds": {
                        "type": "integer",
                        "description": "Wait duration in ms for wait_for_navigation fallback.",
                    },
                },
                "required": ["action"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        return await self._engine(tool_input)

    async def close(self) -> None:
        pass
