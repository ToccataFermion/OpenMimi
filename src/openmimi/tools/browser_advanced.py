"""Browser advanced facade: network, storage, CDP, sessions, emulation."""
from __future__ import annotations

from typing import Any

from .base import ToolBase
from .result import ToolResult


class BrowserAdvancedTool(ToolBase):
    """Thin facade over AgentBrowserTool — advanced/debug actions."""

    name = "browser_advanced"

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Advanced browser features: network interception, storage, "
                "CDP commands, sessions, cache clearing, and device emulation."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "wait",
                            "wait_for",
                            "wait_for_disappear",
                            "wait_for_network_idle",
                            "network_log",
                            "network_modify",
                            "storage",
                            "clear_cache",
                            "save_session",
                            "load_session",
                            "set_timezone",
                            "set_locale",
                            "set_geolocation",
                            "cdp",
                            "batch",
                        ],
                        "description": "Advanced browser action.",
                    },
                    "ref": {"type": "string", "description": "Accessibility ref for wait_for / wait_for_disappear."},
                    "target_text": {"type": "string", "description": "Text to wait for."},
                    "text": {"type": "string", "description": "Generic text to wait for."},
                    "milliseconds": {"type": "integer", "description": "Duration for wait or network_log."},
                    "duration_ms": {"type": "integer", "description": "Duration in ms."},
                    "idle_duration_ms": {"type": "integer", "description": "Idle threshold for wait_for_network_idle."},
                    "filter": {"type": "string", "description": "URL filter for network_log."},
                    "modify_action": {
                        "type": "string",
                        "enum": ["inject_headers", "block_urls", "mock_response", "user_agent"],
                        "description": "Network modification type.",
                    },
                    "headers": {"type": "object", "description": "Headers to inject."},
                    "url_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "URL patterns to block.",
                    },
                    "user_agent": {"type": "string", "description": "User-Agent override."},
                    "storage_action": {
                        "type": "string",
                        "enum": ["get", "set", "delete", "clear"],
                        "description": "Storage operation.",
                    },
                    "storage_type": {
                        "type": "string",
                        "enum": ["localStorage", "sessionStorage", "cookies"],
                        "description": "Storage type.",
                    },
                    "storage_key": {"type": "string", "description": "Storage key."},
                    "storage_value": {"type": "string", "description": "Storage value for set."},
                    "file_path": {"type": "string", "description": "Path for save_session / load_session."},
                    "timezone": {"type": "string", "description": "Timezone (e.g. Asia/Shanghai). Pass empty string to reset."},
                    "locale": {"type": "string", "description": "Locale (e.g. zh-CN). Pass empty string to reset."},
                    "latitude": {"type": "number", "description": "Geolocation latitude."},
                    "longitude": {"type": "number", "description": "Geolocation longitude."},
                    "accuracy": {"type": "number", "description": "Geolocation accuracy in meters."},
                    "cdp_method": {"type": "string", "description": "CDP method name."},
                    "cdp_params": {"type": "object", "description": "CDP parameters."},
                    "steps": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Array of sub-tool calls for batch action.",
                    },
                },
                "required": ["action"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        return await self._engine(tool_input)

    async def close(self) -> None:
        pass
