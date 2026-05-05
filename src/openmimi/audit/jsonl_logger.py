"""Append-only JSONL audit logger and screenshot store."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class JsonlAuditLogger:
    def __init__(self, *, audit_dir: Path, screen_dir: Path) -> None:
        self._audit_dir = audit_dir
        self._screen_dir = screen_dir

    def log_tool_call(
        self,
        *,
        session_id: str,
        step: int,
        tool: str,
        tool_input: dict[str, Any],
        result_summary: str,
        is_error: bool,
        error_code: str | None,
        image_path: str | None,
        duration_ms: int,
    ) -> None:
        raise NotImplementedError("M1: append a JSONL record")

    def save_screenshot(self, *, session_id: str, step: int, png_bytes: bytes) -> str:
        raise NotImplementedError("M1: save screenshot under screen_dir/<session>/step_<n>.png")
