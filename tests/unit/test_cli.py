"""Unit tests for the CLI commands."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openmimi.cli import app

runner = CliRunner()


def test_run_without_api_key_exits_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("openmimi.cli._maybe_load_dotenv", lambda: None)

    result = runner.invoke(app, ["run", "do something"])
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.stderr or "ANTHROPIC_API_KEY" in (
        result.output or ""
    )


def test_replay_missing_session_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openmimi.config.schema import AppConfig

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.audit_dir.mkdir(parents=True)
    monkeypatch.setattr("openmimi.cli.load_config", lambda: cfg)

    result = runner.invoke(app, ["replay", "nope"])
    assert result.exit_code == 1


def test_replay_renders_jsonl_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openmimi.config.schema import AppConfig

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.audit_dir.mkdir(parents=True)
    monkeypatch.setattr("openmimi.cli.load_config", lambda: cfg)

    sid = "abc123"
    audit_file = cfg.storage.audit_dir / f"{sid}.jsonl"
    records = [
        {
            "ts": "2026-05-05T10:00:00.000+00:00",
            "session_id": sid,
            "step": 1,
            "tool": "browser",
            "tool_input": {"action": "navigate", "url": "https://example.com"},
            "result_summary": "Navigated to https://example.com",
            "is_error": False,
            "error_code": None,
            "image_path": "screens/abc123/step_1.png",
            "duration_ms": 412,
        },
        {
            "ts": "2026-05-05T10:00:01.000+00:00",
            "session_id": sid,
            "step": 2,
            "tool": "browser",
            "tool_input": {"action": "click"},
            "result_summary": "text 'Login' not found",
            "is_error": True,
            "error_code": "TARGET_NOT_FOUND",
            "image_path": None,
            "duration_ms": 80,
        },
    ]
    audit_file.write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8"
    )

    result = runner.invoke(app, ["replay", sid])
    assert result.exit_code == 0
    out = result.output
    assert "step   1 [OK ]" in out
    assert "step   2 [ERR]" in out
    assert "TARGET_NOT_FOUND" in out
    assert "screens/abc123/step_1.png" in out
