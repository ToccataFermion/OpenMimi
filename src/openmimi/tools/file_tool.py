"""File tool: read and write local files."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import ToolBase
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Read from and write to the local filesystem. "
    "Use this for inspecting code, logs, configs, and saving results. "
    "Paths should be absolute or relative to the working directory."
)

_MAX_READ_BYTES = 100_000
_MAX_WRITE_BYTES = 500_000


class FileTool(ToolBase):
    """Simple file read/write for agent workflows."""

    name = "file"

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": _TOOL_DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "write", "append", "list", "exists"],
                        "description": "File operation to perform.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content for write/append actions.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line offset for read (1-based, default 1).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read (default 200).",
                    },
                },
                "required": ["action", "path"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        action = tool_input.get("action", "")
        path = str(tool_input.get("path", ""))
        if not path:
            return ToolResult(output="path is required", is_error=True)

        p = Path(path)

        try:
            if action == "read":
                return self._do_read(p, tool_input)
            elif action == "write":
                return self._do_write(p, tool_input)
            elif action == "append":
                return self._do_append(p, tool_input)
            elif action == "list":
                return self._do_list(p)
            elif action == "exists":
                return self._do_exists(p)
            else:
                return ToolResult(output=f"Unknown file action: {action}", is_error=True)
        except Exception as exc:
            return ToolResult(
                output=f"File error: {exc.__class__.__name__}: {exc}",
                is_error=True,
            )

    def _do_read(self, p: Path, inp: dict[str, Any]) -> ToolResult:
        if not p.exists():
            return ToolResult(output=f"File not found: {p}", is_error=True)
        if p.is_dir():
            return ToolResult(output=f"Path is a directory: {p}", is_error=True)

        content = p.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        offset = max(1, int(inp.get("offset", 1)))
        limit = max(1, min(int(inp.get("limit", 200)), 1000))

        start_idx = offset - 1
        end_idx = start_idx + limit
        selected = lines[start_idx:end_idx]

        result = "\n".join(selected)
        truncated = len(lines) > end_idx
        prefix = f"Lines {offset}-{min(offset + limit - 1, len(lines))} of {len(lines)}\n---\n"
        suffix = "\n---\n[truncated]" if truncated else ""
        return ToolResult(output=prefix + result + suffix)

    def _do_write(self, p: Path, inp: dict[str, Any]) -> ToolResult:
        content = str(inp.get("content", ""))
        if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return ToolResult(output="Content too large (>500KB)", is_error=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return ToolResult(output=f"Wrote {len(content)} chars to {p}")

    def _do_append(self, p: Path, inp: dict[str, Any]) -> ToolResult:
        content = str(inp.get("content", ""))
        if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return ToolResult(output="Content too large (>500KB)", is_error=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(output=f"Appended {len(content)} chars to {p}")

    def _do_list(self, p: Path) -> ToolResult:
        if not p.exists():
            return ToolResult(output=f"Directory not found: {p}", is_error=True)
        if not p.is_dir():
            return ToolResult(output=f"Path is not a directory: {p}", is_error=True)
        entries = []
        for child in sorted(p.iterdir()):
            kind = "dir" if child.is_dir() else "file"
            entries.append(f"{kind:4} {child.name}")
        return ToolResult(output="\n".join(entries) or "(empty directory)")

    def _do_exists(self, p: Path) -> ToolResult:
        exists = p.exists()
        kind = ""
        if exists:
            kind = "directory" if p.is_dir() else "file"
        return ToolResult(output=f"{'Exists' if exists else 'Not found'}: {p}" + (f" ({kind})" if kind else ""))
