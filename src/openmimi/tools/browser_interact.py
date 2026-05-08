"""Browser interaction facade: click, type, scroll, drag, upload, etc."""
from __future__ import annotations

from typing import Any

from .base import ToolBase
from .result import ToolResult


class BrowserInteractTool(ToolBase):
    """Thin facade over AgentBrowserTool — only element-interaction actions."""

    name = "browser_interact"

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Interact with page elements: click, type, fill, scroll, drag, upload, etc. "
                "Use ref (from snapshot) or target_text for element targeting."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "click",
                            "right_click",
                            "double_click",
                            "check",
                            "uncheck",
                            "type",
                            "fill",
                            "react_fill",
                            "press",
                            "key_combo",
                            "hover",
                            "scroll",
                            "human_scroll",
                            "scroll_until",
                            "drag",
                            "mouse",
                            "scroll_into_view",
                            "select",
                            "upload",
                            "download",
                        ],
                        "description": "Element interaction action.",
                    },
                    "ref": {"type": "string", "description": "Accessibility ref from snapshot (@eN)."},
                    "target_text": {"type": "string", "description": "Text to find and target."},
                    "target_hint": {"type": "string", "description": "Optional hint to disambiguate target_text."},
                    "value": {"type": "string", "description": "Text value for type / fill / react_fill / select."},
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Key names for press or key_combo (e.g. ['Control','a']).",
                    },
                    "to_ref": {"type": "string", "description": "Destination ref for drag."},
                    "to_target_text": {"type": "string", "description": "Destination text for drag."},
                    "file_path": {"type": "string", "description": "Local file path for upload."},
                    "save_path": {"type": "string", "description": "Local save path for download."},
                    "direction": {"type": "string", "description": "Scroll direction: up/down/left/right."},
                    "amount": {"type": "integer", "description": "Scroll amount in pixels."},
                    "step_pixels": {"type": "integer", "description": "Scroll step size for scroll_until."},
                    "timeout_ms": {"type": "integer", "description": "Timeout for scroll_until."},
                    "interval_ms": {"type": "integer", "description": "Poll interval for scroll_until."},
                    "mouse_action": {
                        "type": "string",
                        "enum": ["move", "down", "up", "wheel"],
                        "description": "Mouse sub-action.",
                    },
                    "x": {"type": "integer", "description": "X coordinate for mouse move."},
                    "y": {"type": "integer", "description": "Y coordinate for mouse move."},
                    "button": {"type": "string", "description": "Mouse button: left/right/middle."},
                    "force": {"type": "boolean", "description": "Use force click (CDP mouse down/up) for stubborn elements."},
                },
                "required": ["action"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        return await self._engine(tool_input)

    async def close(self) -> None:
        pass
