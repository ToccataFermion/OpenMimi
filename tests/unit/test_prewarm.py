"""Tests for the explicit daemon-prewarm hook on Orchestrator + CLI."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openmimi.audit import JsonlAuditLogger
from openmimi.config.schema import AppConfig
from openmimi.orchestrator import Orchestrator
from openmimi.tools.collection import ToolCollection


class _DummyEngine:
    """Minimal stand-in for AgentBrowserTool.is_warming_up()."""

    def __init__(self, warming: bool) -> None:
        self.warming = warming
        self.calls = 0

    def is_warming_up(self) -> bool:
        self.calls += 1
        return self.warming


class _ExplodingEngine:
    """Engine whose is_warming_up raises — must not crash the CLI."""

    def is_warming_up(self) -> bool:  # pragma: no cover — exercised in tests
        raise RuntimeError("warmup status query failed")


def _orch(tmp_path: Path, engine: Any | None) -> Orchestrator:
    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.screen_dir = tmp_path / "screens"
    audit = JsonlAuditLogger(
        audit_dir=cfg.storage.audit_dir, screen_dir=cfg.storage.screen_dir
    )
    return Orchestrator(
        config=cfg,
        llm=object(),  # never called by prewarm_browser
        tools=ToolCollection(),
        audit=audit,
        browser_engine=engine,
    )


def test_prewarm_returns_false_with_no_engine(tmp_path: Path) -> None:
    orch = _orch(tmp_path, engine=None)
    assert orch.prewarm_browser() is False


def test_prewarm_returns_true_when_engine_warming(tmp_path: Path) -> None:
    engine = _DummyEngine(warming=True)
    orch = _orch(tmp_path, engine=engine)
    assert orch.prewarm_browser() is True
    assert engine.calls == 1


def test_prewarm_returns_false_when_engine_done(tmp_path: Path) -> None:
    engine = _DummyEngine(warming=False)
    orch = _orch(tmp_path, engine=engine)
    assert orch.prewarm_browser() is False


def test_prewarm_swallows_exceptions(tmp_path: Path) -> None:
    """Failed status queries must not break REPL startup."""
    orch = _orch(tmp_path, engine=_ExplodingEngine())
    assert orch.prewarm_browser() is False


def test_announce_prewarm_prints_when_warming(
    tmp_path: Path, capsys: Any
) -> None:
    from openmimi.cli import _announce_prewarm

    orch = _orch(tmp_path, engine=_DummyEngine(warming=True))
    _announce_prewarm(orch)
    out = capsys.readouterr().out
    assert "browser" in out
    assert "warming up" in out


def test_announce_prewarm_silent_when_done(tmp_path: Path, capsys: Any) -> None:
    from openmimi.cli import _announce_prewarm

    orch = _orch(tmp_path, engine=_DummyEngine(warming=False))
    _announce_prewarm(orch)
    out = capsys.readouterr().out
    assert "warming up" not in out


def test_announce_prewarm_silent_when_orch_raises(
    tmp_path: Path, capsys: Any
) -> None:
    """Defensive: a bad orchestrator must never break the welcome banner."""
    from openmimi.cli import _announce_prewarm

    class _Bad:
        def prewarm_browser(self) -> bool:
            raise RuntimeError("orch is in a bad state")

    _announce_prewarm(_Bad())
    out = capsys.readouterr().out
    assert "warming up" not in out
