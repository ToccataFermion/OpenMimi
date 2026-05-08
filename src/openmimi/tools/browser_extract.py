"""Browser extraction facade: snapshots, screenshots, DOM queries, page info."""
from __future__ import annotations

from typing import Any

from .base import ToolBase
from .result import ToolResult


class BrowserExtractTool(ToolBase):
    """Thin facade over AgentBrowserTool — only observation/extraction actions."""

    name = "browser_extract"

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Extract information from the page: snapshots, screenshots, "
                "DOM queries (get_url, get_title, get_attribute), and structured extraction."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "snapshot",
                            "screenshot",
                            "extract",
                            "page_source",
                            "get_url",
                            "get_title",
                            "get_attribute",
                            "set_attribute",
                            "get_property",
                            "get_box",
                            "is_visible",
                            "visual_locate",
                            "eval",
                            "console",
                            "pdf",
                        ],
                        "description": "Extraction or observation action.",
                    },
                    "ref": {"type": "string", "description": "Accessibility ref from snapshot (@eN)."},
                    "target_text": {"type": "string", "description": "Text to find and target."},
                    "target_hint": {"type": "string", "description": "Optional hint to disambiguate target_text."},
                    "instruction": {
                        "type": "string",
                        "description": "Extraction instruction: text, headings, links, forms, tables, metadata, images.",
                    },
                    "attribute_name": {"type": "string", "description": "DOM attribute name (href, src, data-id, etc.)."},
                    "attribute_value": {"type": "string", "description": "Value to set for set_attribute."},
                    "property_name": {"type": "string", "description": "JS property name (value, checked, innerText, innerHTML)."},
                    "template_path": {"type": "string", "description": "Image template path for visual_locate."},
                    "click": {"type": "boolean", "description": "Click on visual_locate match."},
                    "confidence": {"type": "number", "description": "Template match confidence threshold (default 0.8)."},
                    "js_code": {"type": "string", "description": "JavaScript code for eval. Wrap multi-line in IIFE."},
                    "file_path": {"type": "string", "description": "Save path for pdf."},
                    "annotate": {"type": "boolean", "description": "Draw accessibility refs on screenshot."},
                },
                "required": ["action"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        return await self._engine(tool_input)

    async def close(self) -> None:
        pass
