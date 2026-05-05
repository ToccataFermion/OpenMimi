"""Unit tests for JsonlAuditLogger."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmimi.audit.jsonl_logger import JsonlAuditLogger


def test_creates_dirs_on_init(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    screen = tmp_path / "screens"
    JsonlAuditLogger(audit_dir=audit, screen_dir=screen)
    assert audit.is_dir()
    assert screen.is_dir()


def test_log_tool_call_appends_jsonl(tmp_path: Path) -> None:
    logger = JsonlAuditLogger(audit_dir=tmp_path / "audit", screen_dir=tmp_path / "s")
    logger.log_tool_call(
        session_id="sess1",
        step=1,
        tool="browser",
        tool_input={"action": "navigate", "url": "https://example.com"},
        result_summary="ok",
        is_error=False,
        error_code=None,
        image_path="screens/sess1/step_1.png",
        duration_ms=120,
    )
    logger.log_tool_call(
        session_id="sess1",
        step=2,
        tool="browser",
        tool_input={"action": "click"},
        result_summary="not found",
        is_error=True,
        error_code="TARGET_NOT_FOUND",
        image_path=None,
        duration_ms=80,
    )

    file = tmp_path / "audit" / "sess1.jsonl"
    lines = file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    rec1 = json.loads(lines[0])
    assert rec1["step"] == 1
    assert rec1["tool"] == "browser"
    assert rec1["tool_input"]["url"] == "https://example.com"
    assert rec1["is_error"] is False
    assert rec1["duration_ms"] == 120
    assert "ts" in rec1 and rec1["ts"].endswith("+00:00")

    rec2 = json.loads(lines[1])
    assert rec2["is_error"] is True
    assert rec2["error_code"] == "TARGET_NOT_FOUND"
    assert rec2["image_path"] is None


def test_separate_sessions_separate_files(tmp_path: Path) -> None:
    logger = JsonlAuditLogger(audit_dir=tmp_path / "a", screen_dir=tmp_path / "s")
    logger.log_tool_call(
        session_id="A",
        step=1,
        tool="browser",
        tool_input={},
        result_summary="x",
        is_error=False,
        error_code=None,
        image_path=None,
        duration_ms=1,
    )
    logger.log_tool_call(
        session_id="B",
        step=1,
        tool="browser",
        tool_input={},
        result_summary="y",
        is_error=False,
        error_code=None,
        image_path=None,
        duration_ms=1,
    )
    files = sorted(p.name for p in (tmp_path / "a").iterdir())
    assert files == ["A.jsonl", "B.jsonl"]


def test_save_screenshot_writes_to_session_dir(tmp_path: Path) -> None:
    logger = JsonlAuditLogger(
        audit_dir=tmp_path / "a", screen_dir=tmp_path / "screens"
    )
    payload = b"\x89PNG\r\n\x1a\nfake-bytes"
    path = logger.save_screenshot(session_id="sess1", step=3, png_bytes=payload)

    p = Path(path)
    assert p.exists()
    assert p.parent.name == "sess1"
    assert p.name == "step_3.png"
    assert p.read_bytes() == payload


def test_log_tool_call_handles_non_ascii(tmp_path: Path) -> None:
    logger = JsonlAuditLogger(audit_dir=tmp_path / "a", screen_dir=tmp_path / "s")
    logger.log_tool_call(
        session_id="zh",
        step=1,
        tool="browser",
        tool_input={"q": "搜索关键字"},
        result_summary="搜索完成",
        is_error=False,
        error_code=None,
        image_path=None,
        duration_ms=10,
    )
    rec = json.loads((tmp_path / "a" / "zh.jsonl").read_text(encoding="utf-8"))
    assert rec["tool_input"]["q"] == "搜索关键字"
    assert rec["result_summary"] == "搜索完成"


@pytest.mark.parametrize("payload", [{"path": Path("/tmp/x")}])
def test_log_tool_call_serialises_path_objects(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    logger = JsonlAuditLogger(audit_dir=tmp_path / "a", screen_dir=tmp_path / "s")
    logger.log_tool_call(
        session_id="p",
        step=1,
        tool="browser",
        tool_input=payload,
        result_summary="ok",
        is_error=False,
        error_code=None,
        image_path=None,
        duration_ms=1,
    )
    rec = json.loads((tmp_path / "a" / "p.jsonl").read_text(encoding="utf-8"))
    assert rec["tool_input"]["path"].startswith("/tmp/x") or rec["tool_input"][
        "path"
    ].startswith("\\tmp\\x")
