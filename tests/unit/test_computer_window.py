"""Unit tests for ``ComputerTool._do_focus_window``.

The xft slider CAPTCHA E2E run on 2026-05-11 exposed that ``computer.
screenshot`` captures whatever is on the user's desktop — not the hidden
Chrome window — so OS-level vision (``detect_color``) had nothing to lock
onto.  ``focus_window`` already exists; this test pins its behavior so the
system-prompt change can rely on it.
"""
from __future__ import annotations

import sys
from typing import Any

import pytest

from openmimi.tools.computer import ComputerTool


class _FakeWin32Gui:
    """Stand-in for the ``win32gui`` module used inside ``_do_focus_window``."""

    def __init__(self, windows: list[tuple[int, str, tuple[int, int, int, int]]]) -> None:
        # windows = [(hwnd, title, (left, top, right, bottom)), ...]
        self._windows = windows
        self.show_calls: list[tuple[int, int]] = []
        self.foreground_calls: list[int] = []

    # API surface used by _do_focus_window:
    def IsWindowVisible(self, hwnd: int) -> bool:  # noqa: N802
        return True

    def GetWindowText(self, hwnd: int) -> str:  # noqa: N802
        for h, title, _ in self._windows:
            if h == hwnd:
                return title
        return ""

    def EnumWindows(self, callback: Any, extra: Any) -> None:  # noqa: N802
        for h, _, _ in self._windows:
            callback(h, extra)

    def ShowWindow(self, hwnd: int, cmd: int) -> None:  # noqa: N802
        self.show_calls.append((hwnd, cmd))

    def SetForegroundWindow(self, hwnd: int) -> None:  # noqa: N802
        self.foreground_calls.append(hwnd)

    def GetWindowRect(self, hwnd: int) -> tuple[int, int, int, int]:  # noqa: N802
        for h, _, rect in self._windows:
            if h == hwnd:
                return rect
        raise RuntimeError(f"unknown hwnd {hwnd}")


class _FakeWin32Con:
    SW_RESTORE = 9
    SW_MINIMIZE = 6
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x2


class _NoopWin32Api:
    """Suppress real Alt-key keystrokes from the foreground-lock workaround
    during tests so test runs don't trigger menu pops on the dev's desktop.
    """

    def keybd_event(self, *_args, **_kwargs) -> None:
        pass

    def GetCurrentThreadId(self) -> int:  # noqa: N802
        return 4242


class _NoopWin32Process:
    def GetWindowThreadProcessId(self, _hwnd: int):  # noqa: N802
        return (4242, 1)

    def AttachThreadInput(self, *_args, **_kwargs) -> None:  # noqa: N802
        pass


@pytest.fixture()
def fake_win32(monkeypatch):
    """Install a fake win32gui/win32con pair in ``sys.modules`` for the test.

    Also stubs ``win32api``/``win32process`` so the new foreground-lock helper
    (see ``openmimi.utils.win_focus``) doesn't reach the real pywin32 modules
    and fire a real Alt keystroke during tests.
    """
    def _install(windows):
        fake_gui = _FakeWin32Gui(windows)
        fake_con = _FakeWin32Con()
        monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
        monkeypatch.setitem(sys.modules, "win32con", fake_con)
        monkeypatch.setitem(sys.modules, "win32api", _NoopWin32Api())
        monkeypatch.setitem(sys.modules, "win32process", _NoopWin32Process())
        return fake_gui

    return _install


@pytest.mark.asyncio
async def test_focus_window_returns_rect_and_calls_set_foreground(fake_win32) -> None:
    fake_gui = fake_win32(
        [
            (101, "Visual Studio Code", (0, 0, 1920, 1080)),
            (202, "招商银行薪福通 - Google Chrome", (1200, 100, 2400, 900)),
        ]
    )
    tool = ComputerTool(screen_dir=None)

    result = await tool._do_focus_window({"title": "薪福通"})

    assert result.is_error is False, result.output
    assert result.details["title"] == "招商银行薪福通 - Google Chrome"
    assert result.details["left"] == 1200
    assert result.details["top"] == 100
    assert result.details["width"] == 1200
    assert result.details["height"] == 800
    # Window was actually brought to front (not just enumerated).
    assert fake_gui.foreground_calls == [202]
    assert (202, _FakeWin32Con.SW_RESTORE) in fake_gui.show_calls


@pytest.mark.asyncio
async def test_focus_window_picks_last_match_when_multiple(fake_win32) -> None:
    """When multiple windows match, the most-recent (last enumerated) wins.

    EnumWindows returns top-level windows in Z-order top-down, so the last
    match is usually the newest tab/instance — useful when several Chrome
    windows are open and we want the one the agent just spawned.
    """
    fake_gui = fake_win32(
        [
            (1, "Chrome - old tab", (0, 0, 100, 100)),
            (2, "Chrome - newer tab", (200, 200, 1000, 1000)),
        ]
    )
    tool = ComputerTool(screen_dir=None)

    result = await tool._do_focus_window({"title": "chrome"})

    assert result.is_error is False
    assert result.details["title"] == "Chrome - newer tab"
    assert fake_gui.foreground_calls == [2]


@pytest.mark.asyncio
async def test_focus_window_reports_no_match_cleanly(fake_win32) -> None:
    fake_gui = fake_win32([(1, "Notepad", (0, 0, 800, 600))])
    tool = ComputerTool(screen_dir=None)

    result = await tool._do_focus_window({"title": "chrome"})

    assert result.is_error is True
    assert "No visible window matching" in result.output
    assert fake_gui.foreground_calls == []


@pytest.mark.asyncio
async def test_focus_window_requires_title(fake_win32) -> None:
    fake_win32([])
    tool = ComputerTool(screen_dir=None)

    result = await tool._do_focus_window({})

    assert result.is_error is True
    assert "title" in result.output.lower()


@pytest.mark.asyncio
async def test_focus_window_match_is_case_insensitive(fake_win32) -> None:
    fake_gui = fake_win32([(7, "Google Chrome", (10, 20, 110, 120))])
    tool = ComputerTool(screen_dir=None)

    result = await tool._do_focus_window({"title": "CHROME"})

    assert result.is_error is False
    assert result.details["title"] == "Google Chrome"
    assert fake_gui.foreground_calls == [7]


@pytest.mark.asyncio
async def test_focus_window_records_method_in_details(fake_win32) -> None:
    """The new helper reports which strategy succeeded — surface it for audit logs."""
    fake_win32([(1, "Google Chrome", (0, 0, 100, 100))])
    tool = ComputerTool(screen_dir=None)

    result = await tool._do_focus_window({"title": "chrome"})

    assert result.is_error is False
    assert result.details["focus_method"] == "direct"
    assert "(direct)" in result.output


@pytest.mark.asyncio
async def test_focus_window_reports_helper_failure_cleanly(monkeypatch) -> None:
    """When every foreground-lock workaround fails, return a clear error
    instead of silently looking like success.
    """

    class _AlwaysFailingGui(_FakeWin32Gui):
        def SetForegroundWindow(self, hwnd: int) -> None:  # noqa: N802
            raise RuntimeError("foreground denied")

        def GetForegroundWindow(self) -> int:  # noqa: N802
            return 999

    fake_gui = _AlwaysFailingGui([(1, "Google Chrome", (0, 0, 100, 100))])
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32con", _FakeWin32Con())
    monkeypatch.setitem(sys.modules, "win32api", _NoopWin32Api())
    monkeypatch.setitem(sys.modules, "win32process", _NoopWin32Process())

    tool = ComputerTool(screen_dir=None)
    result = await tool._do_focus_window({"title": "chrome"})

    assert result.is_error is True
    assert "foreground-lock" in result.output
    assert "failed" in result.output
