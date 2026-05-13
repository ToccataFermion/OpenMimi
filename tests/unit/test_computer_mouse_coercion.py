"""Regression tests for LLM-supplied string/float coords on mouse actions.

The xft slider CAPTCHA run in 2026-05 surfaced a real failure: the model
serialised ``start_x`` as the string ``"530"`` and ``_do_mouse_drag`` then
crashed with ``unsupported operand type(s) for /: 'str' and 'int'`` when it
tried to compute the Euclidean distance.  These tests pin the new defensive
coercion path so the bug cannot silently come back.
"""
from __future__ import annotations

import pytest

from openmimi.tools.computer import (
    ComputerTool,
    ToolResult,
    _coerce_int,
    _coerce_optional_int,
)


def test_coerce_int_handles_common_llm_serialisations() -> None:
    assert _coerce_int(5) == 5
    assert _coerce_int("5") == 5
    assert _coerce_int(" 12 ") == 12
    assert _coerce_int("530") == 530
    assert _coerce_int(5.7) == 5  # truncates toward zero
    assert _coerce_int("5.7") == 5
    assert _coerce_int(True) == 1
    assert _coerce_int(False) == 0
    # Bad / missing → falls back to default rather than raising.
    assert _coerce_int(None) == 0
    assert _coerce_int(None, default=42) == 42
    assert _coerce_int("not-a-number", default=7) == 7
    assert _coerce_int("", default=3) == 3


def test_coerce_optional_int_keeps_none_but_coerces_strings() -> None:
    assert _coerce_optional_int(None) is None
    assert _coerce_optional_int("0") == 0
    assert _coerce_optional_int("530") == 530
    assert _coerce_optional_int(42) == 42


@pytest.mark.asyncio
async def test_mouse_drag_accepts_string_coordinates(monkeypatch) -> None:
    """Drag must not raise TypeError when coords arrive as numeric strings.

    This is the exact shape that failed live on xft:
        {"start_x": "530", "start_y": "525", "end_x": 895, "end_y": 525, ...}
    """
    tool = ComputerTool(screen_dir=None)

    move_calls: list[tuple[int, int]] = []

    async def fake_move(inp):
        # Make sure inputs reaching _do_mouse_move are already ints.
        assert isinstance(inp["x"], int), f"x not coerced: {inp['x']!r}"
        assert isinstance(inp["y"], int), f"y not coerced: {inp['y']!r}"
        move_calls.append((inp["x"], inp["y"]))
        return ToolResult(output="moved")

    async def fake_down(inp):
        return ToolResult(output="down")

    async def fake_up(inp):
        return ToolResult(output="up")

    monkeypatch.setattr(tool, "_do_mouse_move", fake_move)
    monkeypatch.setattr(tool, "_do_mouse_down", fake_down)
    monkeypatch.setattr(tool, "_do_mouse_up", fake_up)

    result = await tool._do_mouse_drag(
        {
            "start_x": "530",
            "start_y": "525",
            "end_x": "895",
            "end_y": 525,
            "steps": "40",
            "delay_ms": "10",
            "humanize": False,
        }
    )

    assert result.is_error is False, result.output
    assert "from (530,525) to (895,525)" in result.output
    # We expect at least the start-pos move and the final-pos move plus the
    # trajectory points in between.
    assert len(move_calls) >= 2


@pytest.mark.asyncio
async def test_mouse_move_accepts_string_coordinates(monkeypatch) -> None:
    """Plain mouse_move must also accept string coords."""
    from openmimi.tools import computer as computer_module

    tool = ComputerTool(screen_dir=None)

    # Stub the screen-size + SendInput surface so the test never touches the
    # actual cursor, then check the coercion path.
    monkeypatch.setattr(computer_module, "_scale_to_abs", lambda x, y: (x, y))

    import ctypes

    class _FakeUser32:
        def SendInput(self, n, ref, size):
            return 1

        def GetCursorPos(self, ref):
            return 1

        def GetSystemMetrics(self, idx):
            return 1920 if idx == 0 else 1080

    class _FakeWinDLL:
        user32 = _FakeUser32()

    monkeypatch.setattr(ctypes, "windll", _FakeWinDLL())

    result = await tool._do_mouse_move({"x": "300", "y": "400"})
    assert result.is_error is False, result.output
    assert "(300, 400)" in result.output


@pytest.mark.asyncio
async def test_mouse_drag_zero_distance_string_inputs(monkeypatch) -> None:
    """start == end with string inputs must take the short-circuit path."""
    tool = ComputerTool(screen_dir=None)

    async def fake_noop(inp):
        return ToolResult(output="noop")

    monkeypatch.setattr(tool, "_do_mouse_move", fake_noop)
    monkeypatch.setattr(tool, "_do_mouse_down", fake_noop)
    monkeypatch.setattr(tool, "_do_mouse_up", fake_noop)

    result = await tool._do_mouse_drag(
        {"start_x": "100", "start_y": "100", "end_x": "100", "end_y": "100"}
    )

    assert result.is_error is False
    assert "from (100,100) to (100,100)" in result.output
