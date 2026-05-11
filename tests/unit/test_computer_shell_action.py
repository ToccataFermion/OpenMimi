"""Regression tests for computer-tool shell + batch dispatch.

cycle 12 (data/audit/3603bd84e4604ff2b97ee47dd54b4b6f.jsonl step 4) caught
two related bugs:

1. ``ComputerTool._do_shell`` referenced ``subprocess.run`` / ``subprocess.
   TimeoutExpired`` without ``subprocess`` being importable from its scope —
   it was only imported function-locally inside ``_do_launch``. Calling the
   action raised ``NameError: name 'subprocess' is not defined``.
2. ``_do_batch`` zeroed out ``is_error`` whenever ``bail`` was false, even
   if every sub-step EXCEPTION'd. This silently masked failures from
   ``mimi audit-stats`` and any other caller that keys off ``is_error``.

These tests pin both behaviours so neither bug can come back.
"""
from __future__ import annotations

import subprocess

import pytest

from openmimi.tools.computer import ComputerTool
from openmimi.tools.result import ToolResult


class _FakeCompletedProcess:
    def __init__(self, stdout: str = "hello\n", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.mark.asyncio
async def test_do_shell_runs_without_nameerror(monkeypatch) -> None:
    """_do_shell must reach subprocess.run, not crash with NameError."""
    tool = ComputerTool(screen_dir=None)
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = await tool._do_shell({"command": "echo hello"})

    assert result.is_error is False, result.output
    assert "STDOUT" in result.output
    assert "hello" in result.output
    assert captured["command"] == "echo hello"
    assert captured["kwargs"].get("shell") is True


@pytest.mark.asyncio
async def test_do_batch_with_shell_substep_does_not_raise_subprocess_nameerror(
    monkeypatch,
) -> None:
    """The exact shape cycle 12 hit — batch dispatch → shell sub-step.

    Before the fix, step 1 of this batch returned
    ``EXCEPTION - name 'subprocess' is not defined`` because _do_shell could
    not see the function-local import. After the fix, the shell step must
    succeed and the failure must NOT appear in the batch summary.
    """
    tool = ComputerTool(screen_dir=None)

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(stdout="ok\n")
    )

    async def fake_screenshot(_inp):
        return ToolResult(output="snap")

    monkeypatch.setattr(tool, "_do_screenshot", fake_screenshot)

    result = await tool._do_batch(
        {
            "bail": False,
            "steps": [
                {"action": "shell", "command": "setx FOO 1"},
                {"action": "screenshot"},
            ],
        }
    )

    assert "EXCEPTION" not in result.output, result.output
    assert "subprocess" not in result.output, result.output
    assert "Step 1 (shell): OK" in result.output
    assert "Step 2 (screenshot): OK" in result.output


@pytest.mark.asyncio
async def test_do_batch_marks_is_error_when_substep_fails_even_with_bail_false(
    monkeypatch,
) -> None:
    """Bail=false means 'keep going', not 'treat failures as success'.

    Before the fix, _do_batch returned is_error=False whenever bail=false,
    even if every sub-step crashed — audit-stats and other downstream
    callers would silently treat the batch as successful.
    """
    tool = ComputerTool(screen_dir=None)

    async def boom_dispatch(action: str, _inp: dict) -> ToolResult:
        # All sub-steps fail with is_error=True.
        return ToolResult(output=f"kaboom-{action}", is_error=True)

    monkeypatch.setattr(tool, "_dispatch", boom_dispatch)

    async def fake_screenshot(_inp):
        return ToolResult(output="snap")

    monkeypatch.setattr(tool, "_do_screenshot", fake_screenshot)

    result = await tool._do_batch(
        {
            "bail": False,
            "steps": [
                {"action": "mouse_move", "x": 1, "y": 1},
                {"action": "mouse_move", "x": 2, "y": 2},
            ],
        }
    )

    assert result.is_error is True, (
        "bail=false must still surface is_error=True when sub-steps failed: "
        f"{result.output!r}"
    )
    assert "ERROR" in result.output
