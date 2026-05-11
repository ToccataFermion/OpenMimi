"""Unit tests for the CLI commands."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openmimi.cli import app

runner = CliRunner()


def test_run_screenshots_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENMIMI_ENABLE_SCREENSHOTS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("openmimi.cli._maybe_load_dotenv", lambda: None)

    result = runner.invoke(app, ["run", "--screenshots", "do something"])
    assert result.exit_code == 2
    assert os.environ.get("OPENMIMI_ENABLE_SCREENSHOTS") == "1"


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


def test_chat_without_api_key_exits_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("openmimi.cli._maybe_load_dotenv", lambda: None)

    result = runner.invoke(app, ["chat"])
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in (result.stderr or "") or "ANTHROPIC_API_KEY" in (
        result.output or ""
    )


def test_chat_one_turn_then_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr("openmimi.cli._maybe_load_dotenv", lambda: None)

    class _FakeOrch:
        def __init__(self) -> None:
            self.turns: list[dict[str, Any]] = []
            self.closed = False

        async def run_chat_turn(self, **kwargs: Any) -> str:
            self.turns.append(kwargs)
            return "assistant reply"

        async def close(self) -> None:
            self.closed = True

    fake = _FakeOrch()

    def _fake_from_env(_cls: object, **_kw: object) -> _FakeOrch:
        return fake

    monkeypatch.setattr(
        "openmimi.orchestrator.Orchestrator.from_env",
        classmethod(_fake_from_env),
    )

    _lines = iter(["hello there", "/exit"])

    monkeypatch.setattr("openmimi.cli._read_chat_line", lambda _p: next(_lines))

    result = runner.invoke(app, ["chat"])
    assert result.exit_code == 0
    assert len(fake.turns) == 1
    assert fake.turns[0]["user_content"] == "hello there"
    assert len(fake.turns[0]["session_id"]) == 32
    assert fake.closed is True
    assert "assistant reply" in result.output


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


def test_audit_stats_renders_table_for_real_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end: ``mimi audit-stats`` reads audit_dir from config and prints rows."""
    from openmimi.config.schema import AppConfig

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.audit_dir.mkdir(parents=True)
    monkeypatch.setattr("openmimi.cli.load_config", lambda: cfg)

    sid = "sess1"
    audit_file = cfg.storage.audit_dir / f"{sid}.jsonl"
    # 3 calls of click: 2 errors → 67%. 3 calls of navigate: 0 errors → 0%.
    records = []
    for i in range(3):
        records.append(
            {
                "ts": f"2026-05-11T10:00:0{i}.000+00:00",
                "session_id": sid,
                "step": i + 1,
                "tool": "agent_browser",
                "tool_input": {"action": "click"},
                "result_summary": "boom" if i < 2 else "ok",
                "is_error": i < 2,
                "error_code": "TARGET_NOT_FOUND" if i < 2 else None,
                "image_path": None,
                "duration_ms": 100,
            }
        )
    for i in range(3):
        records.append(
            {
                "ts": f"2026-05-11T11:00:0{i}.000+00:00",
                "session_id": sid,
                "step": 4 + i,
                "tool": "agent_browser",
                "tool_input": {"action": "navigate", "url": "https://x"},
                "result_summary": "ok",
                "is_error": False,
                "error_code": None,
                "image_path": None,
                "duration_ms": 200,
            }
        )
    audit_file.write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8"
    )

    result = runner.invoke(app, ["audit-stats", "--since", "0", "--min-calls", "1"])
    assert result.exit_code == 0
    out = result.output
    assert "click" in out
    assert "navigate" in out
    # Worst-first ordering: click (67%) before navigate (0%).
    assert out.index("click") < out.index("navigate")
    assert "67%" in out
    assert "TARGET_NOT_FOUND" in out


def test_chat_main_routes_subcommands_to_typer_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``mimi run "..."`` must dispatch to the run subcommand, not the chat REPL.

    The 2026-05-11 goldset cycle 0 caught this: ``mimi`` was bound to
    ``chat_main`` in pyproject.toml, and chat_main previously ignored argv and
    unconditionally started the chat REPL. The goldset cron ran ``mimi run
    "<task>"``, got the welcome banner, hit stdin EOF, and exited with no
    audit log produced. chat_main now sniffs argv[1] for a registered typer
    subcommand and routes there instead.
    """
    import sys
    from openmimi import cli

    called: dict[str, Any] = {}

    def _fake_app() -> None:
        called["argv"] = list(sys.argv)

    monkeypatch.setattr("openmimi.cli.app", _fake_app)
    monkeypatch.setattr(
        "openmimi.cli._known_subcommands",
        lambda: {"run", "chat", "replay", "audit-stats"},
    )
    monkeypatch.setattr("openmimi.cli._polish_console_io", lambda: None)
    monkeypatch.setattr("openmimi.cli._maybe_load_dotenv", lambda: None)
    monkeypatch.setattr(sys, "argv", ["mimi", "run", "do something"])

    cli.chat_main()

    assert called.get("argv") == ["mimi", "run", "do something"]


def test_chat_main_routes_help_flag_to_typer_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``mimi --help`` must show typer help, not drop into the chat REPL."""
    import sys
    from openmimi import cli

    called: dict[str, Any] = {}

    def _fake_app() -> None:
        called["hit"] = True

    monkeypatch.setattr("openmimi.cli.app", _fake_app)
    monkeypatch.setattr(
        "openmimi.cli._known_subcommands",
        lambda: {"run", "chat", "replay", "audit-stats"},
    )
    monkeypatch.setattr("openmimi.cli._polish_console_io", lambda: None)
    monkeypatch.setattr("openmimi.cli._maybe_load_dotenv", lambda: None)
    monkeypatch.setattr(sys, "argv", ["mimi", "--help"])

    cli.chat_main()

    assert called.get("hit") is True


def test_chat_main_bare_invocation_skips_typer_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare ``mimi`` with no args must still go to the chat REPL.

    Regression guard: when fixing ``mimi run`` dispatch we must not break the
    short-cut behaviour where typing just ``mimi`` opens the chat loop.
    """
    import sys
    from openmimi import cli

    called: dict[str, Any] = {}

    def _fake_app() -> None:
        called["hit"] = True

    monkeypatch.setattr("openmimi.cli.app", _fake_app)
    monkeypatch.setattr(
        "openmimi.cli._known_subcommands",
        lambda: {"run", "chat", "replay", "audit-stats"},
    )
    monkeypatch.setattr("openmimi.cli._polish_console_io", lambda: None)
    monkeypatch.setattr("openmimi.cli._maybe_load_dotenv", lambda: None)
    # Make from_env fail loudly so we know chat_main entered its body but did
    # not call _fake_app.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["mimi"])

    with pytest.raises(SystemExit) as ei:
        cli.chat_main()

    # chat_main hits Orchestrator.from_env, which raises without an API key,
    # and exits 2. The important assertion is that it did NOT route to app().
    assert called.get("hit") is None
    assert ei.value.code == 2


def test_audit_stats_min_calls_hides_low_volume_buckets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openmimi.config.schema import AppConfig

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.audit_dir.mkdir(parents=True)
    monkeypatch.setattr("openmimi.cli.load_config", lambda: cfg)

    sid = "sess2"
    audit_file = cfg.storage.audit_dir / f"{sid}.jsonl"
    rec = {
        "ts": "2026-05-11T10:00:00.000+00:00",
        "session_id": sid,
        "step": 1,
        "tool": "computer",
        "tool_input": {"action": "screenshot"},
        "result_summary": "ok",
        "is_error": False,
        "error_code": None,
        "image_path": None,
        "duration_ms": 50,
    }
    audit_file.write_text(json.dumps(rec), encoding="utf-8")

    result = runner.invoke(
        app, ["audit-stats", "--since", "0", "--min-calls", "5"]
    )
    assert result.exit_code == 0
    assert "no matching records" in result.output

