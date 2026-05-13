"""Regression test for shell-mode argv quoting in AgentBrowserTool._exec.

The 2026-05-06/07 audit logs show 6 consecutive ``agent_browser:batch`` failures
that all came back with ``Missing arguments for: mouse``. Root cause: when
``_use_shell=True`` (the fallback path used on Windows when the native
``agent-browser-*.exe`` is not found and we have to invoke the ``.cmd`` wrapper),
``_exec`` was joining argv with ``" ".join(...)``. That re-splits whitespace
inside multi-word argv elements (like batch steps ``"mouse move 100 200"``) at
the shell layer, so the four tokens each become a separate ``step`` to
agent-browser and the command degrades.

The fix replaces ``" ".join(...)`` with ``subprocess.list2cmdline(...)`` so
multi-word args are properly quoted before reaching cmd.exe. This test pins
that behaviour so a future "let's simplify the join" refactor can't silently
re-introduce the bug.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest


def _make_tool(tmp_path: Path):
    """Construct AgentBrowserTool with warmup short-circuited.

    The constructor unconditionally fires a background warmup thread that calls
    ``subprocess.run``. We patch ``subprocess.run`` BEFORE construction so the
    warmup invocation is harmless, then return the tool ready for ``_exec``
    assertions.
    """
    from openmimi.tools import agent_browser as ab

    tool = ab.AgentBrowserTool(
        download_dir=str(tmp_path / "downloads"),
        user_data_dir=str(tmp_path / "profile"),
        executable="fake-agent-browser",
    )
    # Force shell mode regardless of whether the native exe was discovered on
    # the host that's running the test.
    tool._use_shell = True
    tool._executable = "fake-agent-browser.cmd"
    # Mark started so _exec doesn't try to autostart the daemon.
    tool._started = True
    return tool


@pytest.mark.asyncio
async def test_exec_shell_mode_quotes_multiword_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")

    from openmimi.tools import agent_browser as ab

    monkeypatch.setattr(ab.subprocess, "run", _fake_run)

    tool = _make_tool(tmp_path)
    # Allow the warmup thread to clear (it also calls subprocess.run, which is
    # the patched stub — harmless). We don't strictly need to wait, but it
    # keeps the captured list tidy.
    if tool._warmup_thread is not None:
        tool._warmup_thread.join(timeout=2.0)
    captured.clear()

    await tool._exec("batch", "--bail", "--json", "mouse move 100 200", "mouse down")

    assert captured, "_exec must invoke subprocess.run"
    args, kwargs = captured[-1]
    # shell=True path: first positional arg is the full command-line string.
    assert kwargs.get("shell") is True
    cmdline = args[0]
    assert isinstance(cmdline, str)
    # The multi-word batch step must survive as a single quoted token, not
    # as four whitespace-split tokens.
    assert '"mouse move 100 200"' in cmdline
    # Sanity check: list2cmdline preserved single-word args unquoted.
    assert " batch " in cmdline


@pytest.mark.asyncio
async def test_exec_non_shell_mode_passes_argv_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the native exe is found (_use_shell=False), argv is passed as a
    list — subprocess handles quoting via the OS API. Sanity-check that path
    still works (it's what the agent uses today on this dev box)."""
    captured: list[Any] = []

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")

    from openmimi.tools import agent_browser as ab

    monkeypatch.setattr(ab.subprocess, "run", _fake_run)

    tool = _make_tool(tmp_path)
    tool._use_shell = False
    if tool._warmup_thread is not None:
        tool._warmup_thread.join(timeout=2.0)
    captured.clear()

    await tool._exec("batch", "--bail", "--json", "mouse move 100 200")

    assert captured
    args, kwargs = captured[-1]
    # In non-shell mode, first positional arg is the argv list.
    assert kwargs.get("shell") is not True
    argv = args[0]
    assert isinstance(argv, list)
    # Multi-word step preserved as a single list element.
    assert "mouse move 100 200" in argv
