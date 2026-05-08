"""Shell tool: run bash commands with safety guardrails."""
from __future__ import annotations

import asyncio
import shlex
from typing import Any

from .base import ToolBase
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Run shell commands in a bash environment. "
    "Use this for file operations (ls, cat, grep), package management (pip, npm), "
    "git operations, and running scripts. "
    "Prefer simple, single-purpose commands. "
    "For long-running commands, consider using timeout."
)

# Commands that are too dangerous to run automatically
_BLOCKED_COMMANDS = {
    "rm", "rmdir", "mkfs", "dd", "format", "fdisk",
    ">", ">>", "|",  # These are shell operators, not commands per se
}

# Patterns that suggest destructive operations
_BLOCKED_PATTERNS = [
    "rm -rf /", "rm -rf ~", "rm -rf /*", ":(){ :|:& };:",
    "dd if=/dev/zero", "mkfs", "format c:",
]


class ShellTool(ToolBase):
    """Execute shell commands with output capture."""

    name = "shell"

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": _TOOL_DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 60).",
                    },
                },
                "required": ["command"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        command = str(tool_input.get("command", "")).strip()
        if not command:
            return ToolResult(output="No command provided.", is_error=True)

        # Safety checks
        lower_cmd = command.lower()
        for pattern in _BLOCKED_PATTERNS:
            if pattern in lower_cmd:
                return ToolResult(
                    output=f"Blocked: command matches dangerous pattern '{pattern}'.",
                    is_error=True,
                )

        timeout = int(tool_input.get("timeout", 60))
        timeout = max(1, min(timeout, 600))  # clamp 1-600s

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=timeout,
            )
            stdout, stderr = await proc.communicate()
            out_text = stdout.decode("utf-8", errors="replace").strip()
            err_text = stderr.decode("utf-8", errors="replace").strip()

            output_parts = []
            if out_text:
                output_parts.append(out_text)
            if err_text:
                output_parts.append(f"[stderr] {err_text}")

            result_text = "\n".join(output_parts) if output_parts else "(no output)"
            is_error = proc.returncode != 0

            return ToolResult(
                output=result_text[:4000],
                is_error=is_error,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                output=f"Command timed out after {timeout}s.",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(
                output=f"Shell error: {exc.__class__.__name__}: {exc}",
                is_error=True,
            )
