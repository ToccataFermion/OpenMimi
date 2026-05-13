"""Tests for the ``force_set_foreground`` foreground-lock workaround helper.

The helper picks between three escalating strategies (direct → ShowWindow
toggle → AttachThreadInput). These tests simulate each failure mode by
installing fake ``win32gui``/``win32api``/``win32process``/``win32con``
modules in ``sys.modules`` so the helper's internal lazy imports resolve to
them instead of the real pywin32 modules.
"""
from __future__ import annotations

import sys
from typing import Any

import pytest

from openmimi.utils.win_focus import force_set_foreground


class _FakeWin32Con:
    SW_RESTORE = 9
    SW_MINIMIZE = 6
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x2


class _FakeWin32Gui:
    """Captures show/foreground calls and lets each test override which
    ``SetForegroundWindow`` attempt should succeed.
    """

    def __init__(
        self,
        *,
        fg_hwnd: int = 999,
        succeed_on_attempt: int | None = 1,
    ) -> None:
        self.fg_hwnd = fg_hwnd
        self.succeed_on_attempt = succeed_on_attempt
        self.set_fg_attempts = 0
        self.show_window_calls: list[tuple[int, int]] = []
        self.foregrounded: list[int] = []

    def ShowWindow(self, hwnd: int, cmd: int) -> None:  # noqa: N802
        self.show_window_calls.append((hwnd, cmd))

    def SetForegroundWindow(self, hwnd: int) -> None:  # noqa: N802
        self.set_fg_attempts += 1
        if (
            self.succeed_on_attempt is None
            or self.set_fg_attempts < self.succeed_on_attempt
        ):
            raise RuntimeError(
                f"foreground-lock denied (attempt {self.set_fg_attempts})"
            )
        self.foregrounded.append(hwnd)

    def GetForegroundWindow(self) -> int:  # noqa: N802
        return self.fg_hwnd


class _FakeWin32Api:
    def __init__(self) -> None:
        self.alt_events: list[tuple[int, int]] = []
        self.my_thread_id = 4242

    def keybd_event(self, vk: int, scan: int, flags: int, extra: int) -> None:
        self.alt_events.append((vk, flags))

    def GetCurrentThreadId(self) -> int:  # noqa: N802
        return self.my_thread_id


class _FakeWin32Process:
    def __init__(self, fg_thread_id: int = 8888) -> None:
        self.fg_thread_id = fg_thread_id
        self.attach_calls: list[tuple[int, int, bool]] = []

    def GetWindowThreadProcessId(self, hwnd: int) -> tuple[int, int]:  # noqa: N802
        # Tuple shape: (thread_id, process_id)
        return (self.fg_thread_id, 1)

    def AttachThreadInput(  # noqa: N802
        self, src: int, dst: int, attach: bool
    ) -> None:
        self.attach_calls.append((src, dst, attach))


@pytest.fixture()
def fake_win32(monkeypatch):
    """Install a fully-fake win32 stack for one test, return the shims."""

    def _install(
        *,
        succeed_on_attempt: int | None = 1,
        include_api: bool = True,
        include_process: bool = True,
    ) -> dict[str, Any]:
        gui = _FakeWin32Gui(succeed_on_attempt=succeed_on_attempt)
        con = _FakeWin32Con()
        monkeypatch.setitem(sys.modules, "win32gui", gui)
        monkeypatch.setitem(sys.modules, "win32con", con)
        shims: dict[str, Any] = {"gui": gui, "con": con}
        if include_api:
            api = _FakeWin32Api()
            monkeypatch.setitem(sys.modules, "win32api", api)
            shims["api"] = api
        else:
            monkeypatch.delitem(sys.modules, "win32api", raising=False)
        if include_process:
            proc = _FakeWin32Process()
            monkeypatch.setitem(sys.modules, "win32process", proc)
            shims["process"] = proc
        else:
            monkeypatch.delitem(sys.modules, "win32process", raising=False)
        return shims

    return _install


def test_direct_path_succeeds_on_first_attempt(fake_win32) -> None:
    shims = fake_win32(succeed_on_attempt=1)
    ok, method = force_set_foreground(hwnd=123)
    assert ok is True
    assert method == "direct"
    # ShowWindow(SW_RESTORE) was called once before the direct attempt,
    # and the toggle path was NOT used.
    assert shims["gui"].show_window_calls == [(123, _FakeWin32Con.SW_RESTORE)]
    # Alt key was sent to unlock the foreground queue.
    assert shims["api"].alt_events == [
        (_FakeWin32Con.VK_MENU, 0),
        (_FakeWin32Con.VK_MENU, _FakeWin32Con.KEYEVENTF_KEYUP),
    ]
    # AttachThreadInput was never needed.
    assert shims["process"].attach_calls == []


def test_toggle_path_recovers_after_direct_fails(fake_win32) -> None:
    shims = fake_win32(succeed_on_attempt=2)
    ok, method = force_set_foreground(hwnd=42)
    assert ok is True
    assert method == "toggle"
    # Restore (step 0) + minimise + restore (step 2) before retrying.
    assert (42, _FakeWin32Con.SW_RESTORE) in shims["gui"].show_window_calls
    assert (42, _FakeWin32Con.SW_MINIMIZE) in shims["gui"].show_window_calls
    # AttachThreadInput still not needed.
    assert shims["process"].attach_calls == []


def test_attach_path_recovers_after_direct_and_toggle_fail(fake_win32) -> None:
    shims = fake_win32(succeed_on_attempt=3)
    ok, method = force_set_foreground(hwnd=77)
    assert ok is True
    assert method == "attached"
    # AttachThreadInput must be called once with attach=True and once with
    # attach=False (the detach in the finally block).
    assert (4242, 8888, True) in shims["process"].attach_calls
    assert (4242, 8888, False) in shims["process"].attach_calls


def test_returns_failed_when_all_strategies_exhausted(fake_win32) -> None:
    shims = fake_win32(succeed_on_attempt=None)  # always raise
    ok, method = force_set_foreground(hwnd=99)
    assert ok is False
    assert method == "failed"
    # Tried all three strategies — at least three SetForegroundWindow calls.
    assert shims["gui"].set_fg_attempts >= 3


def test_skips_alt_keystroke_when_win32api_missing(fake_win32) -> None:
    """Partial pywin32 installs (gui only) shouldn't crash the helper."""
    shims = fake_win32(succeed_on_attempt=1, include_api=False)
    ok, method = force_set_foreground(hwnd=10)
    assert ok is True
    assert method == "direct"
    assert "api" not in shims  # confirm shim setup


def test_attach_path_skipped_when_win32process_missing(fake_win32) -> None:
    """No win32process → attached fallback is unavailable; we just return failed."""
    fake_win32(succeed_on_attempt=None, include_process=False)
    ok, method = force_set_foreground(hwnd=10)
    assert ok is False
    assert method == "failed"


def test_returns_missing_when_win32gui_unavailable(monkeypatch) -> None:
    """No pywin32 at all (e.g. on non-Windows CI) → clean (False, 'win32gui_missing')."""
    # Force the lazy `import win32gui` inside force_set_foreground to fail.
    monkeypatch.setitem(sys.modules, "win32gui", None)
    monkeypatch.setitem(sys.modules, "win32con", None)
    ok, method = force_set_foreground(hwnd=1)
    assert ok is False
    assert method == "win32gui_missing"


def test_attach_skips_when_already_foreground_thread(monkeypatch) -> None:
    """If our thread already owns the foreground, AttachThreadInput is skipped
    but SetForegroundWindow is still attempted directly.
    """
    gui = _FakeWin32Gui(succeed_on_attempt=3, fg_hwnd=555)
    con = _FakeWin32Con()
    api = _FakeWin32Api()
    proc = _FakeWin32Process(fg_thread_id=api.my_thread_id)  # same thread
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32con", con)
    monkeypatch.setitem(sys.modules, "win32api", api)
    monkeypatch.setitem(sys.modules, "win32process", proc)

    ok, method = force_set_foreground(hwnd=5)
    assert ok is True
    assert method == "attached"
    # No actual attach happened (same thread); detach list should be empty.
    assert proc.attach_calls == []
