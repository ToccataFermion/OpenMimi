"""Unit tests for the vision actions added to ComputerTool.

These mock mss so the tests run without a real display and pin the byte-order
contract: mss ScreenShot.rgb is RGB (3 bytes/pixel), and get_pixel_color /
detect_color must map indexes 0,1,2 → R,G,B respectively.

numpy is imported unconditionally — it is a hard dependency of openmimi
(see pyproject.toml). If the import fails, the regression is that someone
removed numpy from the install requirements, which would silently disable
detect_color / wait_for_change / get_pixel_color at runtime (cycle 119).
"""
from __future__ import annotations

import pytest

import numpy as np  # hard dep; do not importorskip

from openmimi.tools.computer import ComputerTool


class _FakeScreenShot:
    def __init__(self, rgb: bytes, width: int, height: int) -> None:
        self.rgb = rgb
        self.width = width
        self.height = height
        self.size = (width, height)


class _FakeMSS:
    """Minimal mss stand-in: monitors[1] is the primary, grab returns canned bytes."""

    def __init__(self, pixels: list[tuple[int, int, int]], width: int, height: int) -> None:
        self._pixels = pixels
        self._width = width
        self._height = height
        self.monitors = [
            {"left": 0, "top": 0, "width": width, "height": height},
            {"left": 0, "top": 0, "width": width, "height": height},
        ]

    def grab(self, region: dict[str, int]) -> _FakeScreenShot:
        w = region["width"]
        h = region["height"]
        # The single-pixel grab in get_pixel_color asks for the top-left of the region;
        # for the test we always serve from self._pixels[0..w*h].
        sub = self._pixels[: w * h]
        rgb_bytes = bytes(c for px in sub for c in px)
        return _FakeScreenShot(rgb_bytes, w, h)


@pytest.mark.asyncio
async def test_get_pixel_color_maps_rgb_correctly() -> None:
    tool = ComputerTool(screen_dir=None)
    # Pure red pixel: R=200, G=10, B=20 in mss .rgb byte order.
    tool._mss = _FakeMSS([(200, 10, 20)], width=1, height=1)

    result = await tool._do_get_pixel_color({"x": 0, "y": 0})

    assert result.is_error is False, result.output
    assert result.details["r"] == 200
    assert result.details["g"] == 10
    assert result.details["b"] == 20
    assert result.details["hex"] == "#c80a14"


@pytest.mark.asyncio
async def test_detect_color_finds_match_in_region() -> None:
    tool = ComputerTool(screen_dir=None)
    # 2x2 region. Top-left is target, others are off.
    pixels = [
        (10, 200, 30),   # target
        (50, 50, 50),
        (50, 50, 50),
        (50, 50, 50),
    ]
    tool._mss = _FakeMSS(pixels, width=2, height=2)

    result = await tool._do_detect_color(
        {"r": 10, "g": 200, "b": 30, "tolerance": 5, "x": 0, "y": 0, "width": 2, "height": 2}
    )

    assert result.is_error is False, result.output
    assert result.details["match_count"] == 1
    assert result.details["first_match"] == {"x": 0, "y": 0}


@pytest.mark.asyncio
async def test_detect_color_reports_zero_matches_cleanly() -> None:
    tool = ComputerTool(screen_dir=None)
    tool._mss = _FakeMSS([(0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0)], width=2, height=2)

    result = await tool._do_detect_color(
        {"r": 255, "g": 255, "b": 255, "tolerance": 0, "x": 0, "y": 0, "width": 2, "height": 2}
    )

    assert result.is_error is False
    assert result.details["match_count"] == 0
    assert "No pixels matching" in result.output


@pytest.mark.asyncio
async def test_wait_for_change_detects_difference_in_region() -> None:
    """First grab is baseline; second grab differs → should report change."""
    tool = ComputerTool(screen_dir=None)

    class _ChangingFakeMSS(_FakeMSS):
        def __init__(self) -> None:
            super().__init__([(0, 0, 0)] * 4, width=2, height=2)
            self._call = 0

        def grab(self, region):
            self._call += 1
            if self._call == 1:
                return _FakeScreenShot(bytes([0] * (4 * 3)), 2, 2)
            return _FakeScreenShot(bytes([255] * (4 * 3)), 2, 2)

    tool._mss = _ChangingFakeMSS()

    result = await tool._do_wait_for_change(
        {"x": 0, "y": 0, "width": 2, "height": 2, "timeout_ms": 500, "interval_ms": 50, "threshold": 0.1}
    )

    assert result.is_error is False, result.output
    assert result.details["change_ratio"] >= 0.1


@pytest.mark.asyncio
async def test_vision_tools_do_not_report_numpy_missing() -> None:
    """Regression: cycle 119 hit `detect_color requires numpy: No module named 'numpy'`.

    numpy is now declared in pyproject.toml dependencies — verify the runtime
    import-check path never fires for any of the vision tools that gate on it.
    If numpy is removed from install requirements, these assertions will
    surface the regression with a clear message instead of silently skipping.
    """
    assert np.__name__ == "numpy"
    tool = ComputerTool(screen_dir=None)
    tool._mss = _FakeMSS([(0, 0, 0)] * 4, width=2, height=2)

    for name, inp in [
        (
            "_do_detect_color",
            {"r": 0, "g": 0, "b": 0, "tolerance": 0, "x": 0, "y": 0, "width": 1, "height": 1},
        ),
        ("_do_get_pixel_color", {"x": 0, "y": 0}),
        (
            "_do_wait_for_change",
            {"x": 0, "y": 0, "width": 1, "height": 1, "timeout_ms": 50, "interval_ms": 25, "threshold": 0.5},
        ),
    ]:
        result = await getattr(tool, name)(inp)
        assert "requires numpy" not in result.output, (
            f"{name} reported missing numpy: {result.output}"
        )
