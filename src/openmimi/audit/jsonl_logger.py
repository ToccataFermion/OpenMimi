"""Append-only JSONL audit logger and screenshot store.

Layout on disk:

    audit_dir/<session_id>.jsonl     # one JSON record per tool call
    screen_dir/<session_id>/step_<n>.png

Each JSONL record has the shape:

    {
        "ts": "2026-05-05T10:48:43.766+00:00",
        "session_id": "...",
        "step": 3,
        "tool": "browser",
        "tool_input": {...},
        "result_summary": "...",
        "is_error": false,
        "error_code": null,
        "image_path": "screens/<sid>/step_3.png",
        "duration_ms": 412
    }
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonlAuditLogger:
    def __init__(self, *, audit_dir: Path, screen_dir: Path) -> None:
        self._audit_dir = Path(audit_dir)
        self._screen_dir = Path(screen_dir)
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._screen_dir.mkdir(parents=True, exist_ok=True)

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
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "session_id": session_id,
            "step": step,
            "tool": tool,
            "tool_input": tool_input,
            "result_summary": result_summary,
            "is_error": is_error,
            "error_code": error_code,
            "image_path": image_path,
            "duration_ms": duration_ms,
        }
        path = self._audit_path(session_id)
        line = json.dumps(record, ensure_ascii=False, default=_json_default)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")

    def save_screenshot(
        self, *, session_id: str, step: int, png_bytes: bytes
    ) -> str:
        session_dir = self._screen_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / f"step_{step}.png"
        path.write_bytes(png_bytes)
        return str(path)

    def _audit_path(self, session_id: str) -> Path:
        return self._audit_dir / f"{session_id}.jsonl"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


__all__ = ["JsonlAuditLogger"]
