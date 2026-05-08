"""Code execution tool: run Python code and return stdout/result.

Executes in a restricted namespace with access to common stdlib modules.
Useful for data processing, calculations, format conversions, and quick
automation scripts.
"""
from __future__ import annotations

import ast
import json
import sys
import traceback
from io import StringIO
from typing import Any

from .base import ToolBase
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Execute Python code and return the output. "
    "Use this for calculations, data processing, format conversions, "
    "image analysis with PIL, or any task that is easier in code than "
    "through shell commands. "
    "The code runs in a restricted environment with access to: json, math, "
    "random, re, datetime, itertools, collections, statistics, typing, "
    "string, hashlib, base64, urllib.parse, and numpy if installed. "
    "Print output is captured and returned. If the last expression is a "
    "value, it is also returned."
)

# Modules safe to expose
_ALLOWED_MODULES = {
    "json", "math", "random", "re", "datetime", "itertools",
    "collections", "statistics", "typing", "string", "hashlib",
    "base64", "urllib.parse", "textwrap", "functools", "decimal",
    "fractions", "enum", "inspect", "types", "copy", "pickle",
    "csv", "io", "pathlib", "warnings", "html",
}


def _build_globals() -> dict[str, Any]:
    """Build a restricted globals dict with safe modules."""
    g: dict[str, Any] = {"__builtins__": __builtins__}
    for name in _ALLOWED_MODULES:
        try:
            mod = __import__(name, fromlist=[""])
            g[name] = mod
        except Exception:
            pass
    # Optional scientific stack
    for name in ("numpy", "np"):
        try:
            import numpy as np  # type: ignore[import-untyped]
            g["numpy"] = np
            g["np"] = np
        except Exception:
            pass
    for name in ("PIL", "Image"):
        try:
            from PIL import Image  # type: ignore[import-untyped]
            g["PIL"] = Image
            g["Image"] = Image
        except Exception:
            pass
    return g


class CodeTool(ToolBase):
    """Execute Python code with captured stdout."""

    name = "code"

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": _TOOL_DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute. Can be multiple lines.",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["python"],
                        "description": "Programming language (default python).",
                    },
                },
                "required": ["code"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        code = str(tool_input.get("code", "")).strip()
        if not code:
            return ToolResult(output="No code provided.", is_error=True)

        # Safety: block obvious dangerous patterns
        blocked = [
            "__import__", "importlib", "os.system", "subprocess", "eval(", "exec(",
            "open(", "compile(", "__builtins__", "__class__", "__base__",
            "__subclasses__", "__globals__", "pty", "socket", "urllib.request",
        ]
        lower = code.lower()
        for b in blocked:
            if b in lower:
                return ToolResult(
                    output=f"Blocked pattern detected in code: {b}", is_error=True
                )

        # Try to parse as AST to catch syntax errors early
        try:
            ast.parse(code)
        except SyntaxError as exc:
            return ToolResult(
                output=f"Syntax error: {exc}", is_error=True
            )

        # Capture stdout
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        buf = StringIO()
        sys.stdout = buf
        sys.stderr = buf

        g = _build_globals()
        result_value = None
        error_text = None

        try:
            # Compile and exec
            compiled = compile(code, "<tool>", "exec")
            exec(compiled, g)  # noqa: S102
            # Try to get the last expression value if the code ends with one
            try:
                last = ast.parse(code).body[-1]
                if isinstance(last, ast.Expr):
                    result_value = eval(compile(ast.Expression(last.value), "<tool>", "eval"), g)  # noqa: S307
            except Exception:
                pass
        except Exception:
            error_text = traceback.format_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        output = buf.getvalue()
        parts: list[str] = []
        if output:
            parts.append(output)
        if error_text:
            parts.append(f"[error] {error_text}")
        if result_value is not None and not parts:
            try:
                parts.append(json.dumps(result_value, ensure_ascii=False, indent=2))
            except Exception:
                parts.append(str(result_value))
        elif result_value is not None:
            try:
                parts.append(f"[result] {json.dumps(result_value, ensure_ascii=False, indent=2)}")
            except Exception:
                parts.append(f"[result] {result_value}")

        text = "\n".join(parts) if parts else "(no output)"
        return ToolResult(
            output=text[:4000],
            is_error=error_text is not None,
        )


__all__ = ["CodeTool"]
