"""Unit tests for ``ComputerTool._do_screenshot`` region support.

Goldset cycle 85 (focus_then_screenshot) surfaced that ``computer.screenshot``
ignored any input and always captured the full primary monitor — there was no
way to crop to a focused window's box.  The agent would call list_windows /
focus_window to get the window rectangle and then have to claim its full-screen
screenshot was "just the window area," which is misleading.  This file pins
the region= parameter so a regression that drops it will fail loudly.

These tests intentionally do NOT depend on numpy (unlike test_computer_vision)
so they run in minimal environments.
"""
from __future__ import annotations

import pytest

from openmimi.tools.computer import ComputerTool


class _FakeScreenShot:
    def __init__(self, rgb: bytes, width: int, height: int) -> None:
        self.rgb = rgb
        self.width = width
        self.height = height
        self.size = (width, height)


class _FakeMSS:
    """Minimal mss stand-in: monitors[1] is the primary, grab records its arg."""

    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
        self.monitors = [
            {"left": 0, "top": 0, "width": width, "height": height},
            {"left": 0, "top": 0, "width": width, "height": height},
        ]
        self.grab_calls: list[dict[str, int]] = []

    def grab(self, region: dict[str, int]) -> _FakeScreenShot:
        self.grab_calls.append(region)
        w = region["width"]
        h = region["height"]
        return _FakeScreenShot(bytes([0] * (w * h * 3)), w, h)


@pytest.mark.asyncio
async def test_screenshot_region_forwards_to_mss_grab(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """screenshot with region= crops to that rectangle (not the full monitor)."""
    monkeypatch.setenv("OPENMIMI_ENABLE_SCREENSHOTS", "1")
    tool = ComputerTool(screen_dir=str(tmp_path))
    tool._mss = _FakeMSS(width=200, height=100)

    result = await tool._do_screenshot(
        {"region": {"left": 10, "top": 20, "width": 50, "height": 40}}
    )

    assert result.is_error is False, result.output
    assert tool._mss.grab_calls == [
        {"left": 10, "top": 20, "width": 50, "height": 40}
    ]
    # The output line should mention the offset so audit consumers can see
    # which area was captured without re-opening the image.
    assert "region=(10,20)" in result.output


@pytest.mark.asyncio
async def test_screenshot_no_region_falls_back_to_primary_monitor(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No region argument → grab monitors[1] (legacy full-screen behavior)."""
    monkeypatch.setenv("OPENMIMI_ENABLE_SCREENSHOTS", "1")
    tool = ComputerTool(screen_dir=str(tmp_path))
    tool._mss = _FakeMSS(width=200, height=100)

    result = await tool._do_screenshot({})

    assert result.is_error is False
    assert tool._mss.grab_calls == [tool._mss.monitors[1]]
    assert "region=" not in result.output


@pytest.mark.asyncio
async def test_screenshot_region_with_zero_size_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-positive width/height is an error, not a silent full-screen grab."""
    monkeypatch.setenv("OPENMIMI_ENABLE_SCREENSHOTS", "1")
    tool = ComputerTool(screen_dir=str(tmp_path))
    tool._mss = _FakeMSS(width=200, height=100)

    result = await tool._do_screenshot(
        {"region": {"left": 0, "top": 0, "width": 0, "height": 10}}
    )

    assert result.is_error is True
    assert "positive width and height" in result.output
    # And we should not have called grab() at all on the bad input.
    assert tool._mss.grab_calls == []
