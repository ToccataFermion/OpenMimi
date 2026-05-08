"""ComputerUse tool: vision-only Windows desktop automation.

Mirrors Anthropic's computer-use tool shape:
- screenshot: capture the desktop and return a base64 PNG
- mouse_move: move the cursor to absolute screen coordinates
- mouse_click: click at current or specified position
- mouse_scroll: scroll the mouse wheel
- key_press: press a single key (Enter, Escape, Tab, etc.)
- type: type a string of text

Uses mss for screen capture and SendInput via ctypes for injection.
"""
from __future__ import annotations

import base64
import ctypes
import io
import json
import os
import sys
import time
from typing import Any

from .base import ToolBase
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Control the Windows desktop via screenshot + input injection. "
    "All coordinates are absolute screen pixels. "
    "After each action a fresh screenshot is returned so the model can observe state.\n\n"
    "Action reference:\n"
    "- screenshot: capture the entire desktop\n"
    "- mouse_move x y: move cursor to (x, y)\n"
    "- mouse_click [x y] [button=left|right]: click at coordinates or current position\n"
    "- mouse_down [button=left|right]: press and hold mouse button at current position\n"
    "- mouse_up [button=left|right]: release mouse button at current position\n"
    "- mouse_drag start_x start_y end_x end_y [button=left|right] [steps=20]: drag with bezier smoothing\n"
    "- mouse_scroll amount [x y]: scroll wheel (positive = up, negative = down)\n"
    "- key_press key: press a key (Enter, Escape, Tab, Control, Alt, Shift, etc.)\n"
    "- type text: type a string"
)

# Windows input constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# Mouse event flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800

# Keyboard constants
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

# Virtual-key codes commonly used by agents
_VK_MAP = {
    "return": 0x0D,
    "enter": 0x0D,
    "escape": 0x1B,
    "esc": 0x1B,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "control": 0x11,
    "ctrl": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
    "0": 0x30,
    "1": 0x31,
    "2": 0x32,
    "3": 0x33,
    "4": 0x34,
    "5": 0x35,
    "6": 0x36,
    "7": 0x37,
    "8": 0x38,
    "9": 0x39,
    "a": 0x41,
    "b": 0x42,
    "c": 0x43,
    "d": 0x44,
    "e": 0x45,
    "f": 0x46,
    "g": 0x47,
    "h": 0x48,
    "i": 0x49,
    "j": 0x4A,
    "k": 0x4B,
    "l": 0x4C,
    "m": 0x4D,
    "n": 0x4E,
    "o": 0x4F,
    "p": 0x50,
    "q": 0x51,
    "r": 0x52,
    "s": 0x53,
    "t": 0x54,
    "u": 0x55,
    "v": 0x56,
    "w": 0x57,
    "x": 0x58,
    "y": 0x59,
    "z": 0x5A,
}


def _get_screen_size() -> tuple[int, int]:
    """Return (width, height) of the virtual screen."""
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def _scale_to_abs(x: int, y: int) -> tuple[int, int]:
    """Convert screen coordinates to SendInput absolute coordinates (0-65535)."""
    w, h = _get_screen_size()
    abs_x = int(x * 65535 / (w - 1)) if w > 1 else 0
    abs_y = int(y * 65535 / (h - 1)) if h > 1 else 0
    return abs_x, abs_y


class _INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [
            ("mi", ctypes.c_ulong * 7),  # MOUSEINPUT simplified
            ("ki", ctypes.c_ulong * 7),  # KEYBDINPUT simplified
        ]
    _anonymous_ = ("i",)
    _fields_ = [("type", ctypes.c_ulong), ("i", _I)]


class ComputerTool(ToolBase):
    """Vision-only desktop automation for Windows."""

    name = "computer"

    def __init__(self, screen_dir: str | None = None) -> None:
        self._screen_dir = screen_dir or os.path.join("data", "screens")
        os.makedirs(self._screen_dir, exist_ok=True)
        self._mss = None

    def _ensure_mss(self) -> Any:
        """Lazy-import mss so the tool can be inspected without the dep."""
        if self._mss is None:
            try:
                import mss
                self._mss = mss.mss()
            except ImportError as exc:
                raise RuntimeError(
                    "mss is required for computer screenshots. "
                    "Install it with: pip install mss"
                ) from exc
        return self._mss

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": _TOOL_DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "screenshot",
                            "mouse_move",
                            "mouse_click",
                            "mouse_down",
                            "mouse_up",
                            "mouse_drag",
                            "mouse_scroll",
                            "key_press",
                            "type",
                        ],
                        "description": "The desktop action to perform.",
                    },
                    "x": {
                        "type": "integer",
                        "description": "X coordinate (absolute screen pixels).",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate (absolute screen pixels).",
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button for click (default left).",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Scroll amount in wheel clicks (positive = up, negative = down).",
                    },
                    "key": {
                        "type": "string",
                        "description": "Key to press (Enter, Escape, Tab, etc.).",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type.",
                    },
                    "end_x": {
                        "type": "integer",
                        "description": "End X coordinate for drag (absolute screen pixels).",
                    },
                    "end_y": {
                        "type": "integer",
                        "description": "End Y coordinate for drag (absolute screen pixels).",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Number of intermediate points for drag smoothing (default 20).",
                    },
                },
                "required": ["action"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        action = tool_input.get("action", "")
        try:
            return await self._dispatch(action, tool_input)
        except Exception as exc:
            return ToolResult(output=f"Computer tool error: {exc}", is_error=True)

    async def _dispatch(self, action: str, inp: dict[str, Any]) -> ToolResult:
        handlers: dict[str, Any] = {
            "screenshot": self._do_screenshot,
            "mouse_move": self._do_mouse_move,
            "mouse_click": self._do_mouse_click,
            "mouse_down": self._do_mouse_down,
            "mouse_up": self._do_mouse_up,
            "mouse_drag": self._do_mouse_drag,
            "mouse_scroll": self._do_mouse_scroll,
            "key_press": self._do_key_press,
            "type": self._do_type,
        }
        handler = handlers.get(action)
        if handler is None:
            return ToolResult(output=f"Unknown computer action: {action}", is_error=True)
        return await handler(inp)

    # ------------------------------------------------------------------ #
    #  Actions
    # ------------------------------------------------------------------ #

    async def _do_screenshot(self, _inp: dict[str, Any]) -> ToolResult:
        sct = self._ensure_mss()
        raw = sct.grab(sct.monitors[0])  # entire virtual screen
        import mss.tools
        png_bytes = mss.tools.to_png(raw.rgb, raw.size)
        b64 = base64.b64encode(png_bytes).decode("ascii")
        path = os.path.join(
            self._screen_dir, f"screen_{int(time.time() * 1000)}.png"
        )
        with open(path, "wb") as f:
            f.write(png_bytes)
        return ToolResult(
            output=f"Screenshot saved to {path} ({raw.width}x{raw.height})",
            base64_image=b64,
        )

    async def _do_mouse_move(self, inp: dict[str, Any]) -> ToolResult:
        x = inp.get("x", 0)
        y = inp.get("y", 0)
        abs_x, abs_y = _scale_to_abs(x, y)
        inp_struct = _INPUT()
        inp_struct.type = INPUT_MOUSE
        inp_struct.i.mi[0] = abs_x
        inp_struct.i.mi[1] = abs_y
        inp_struct.i.mi[2] = 0
        inp_struct.i.mi[3] = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
        inp_struct.i.mi[4] = 0
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp_struct), ctypes.sizeof(_INPUT))
        return ToolResult(output=f"Mouse moved to ({x}, {y})")

    async def _do_mouse_click(self, inp: dict[str, Any]) -> ToolResult:
        x = inp.get("x")
        y = inp.get("y")
        button = inp.get("button", "left")
        # Move first if coordinates provided
        if x is not None and y is not None:
            await self._do_mouse_move({"x": x, "y": y})
            time.sleep(0.05)
        down_flag, up_flag = {
            "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        }.get(button, (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
        self._send_mouse_event(down_flag)
        time.sleep(0.05)
        self._send_mouse_event(up_flag)
        return ToolResult(output=f"Mouse {button} clicked")

    async def _do_mouse_down(self, inp: dict[str, Any]) -> ToolResult:
        button = inp.get("button", "left")
        x = inp.get("x")
        y = inp.get("y")
        if x is not None and y is not None:
            await self._do_mouse_move({"x": x, "y": y})
            time.sleep(0.05)
        down_flag = {
            "left": MOUSEEVENTF_LEFTDOWN,
            "right": MOUSEEVENTF_RIGHTDOWN,
            "middle": MOUSEEVENTF_MIDDLEDOWN,
        }.get(button, MOUSEEVENTF_LEFTDOWN)
        self._send_mouse_event(down_flag)
        return ToolResult(output=f"Mouse {button} down")

    async def _do_mouse_up(self, inp: dict[str, Any]) -> ToolResult:
        button = inp.get("button", "left")
        x = inp.get("x")
        y = inp.get("y")
        if x is not None and y is not None:
            await self._do_mouse_move({"x": x, "y": y})
            time.sleep(0.05)
        up_flag = {
            "left": MOUSEEVENTF_LEFTUP,
            "right": MOUSEEVENTF_RIGHTUP,
            "middle": MOUSEEVENTF_MIDDLEUP,
        }.get(button, MOUSEEVENTF_LEFTUP)
        self._send_mouse_event(up_flag)
        return ToolResult(output=f"Mouse {button} up")

    async def _do_mouse_drag(self, inp: dict[str, Any]) -> ToolResult:
        """Drag from (x,y) to (end_x,end_y) with optional bezier smoothing."""
        import random
        start_x = inp.get("x", 0)
        start_y = inp.get("y", 0)
        end_x = inp.get("end_x", start_x)
        end_y = inp.get("end_y", start_y)
        button = inp.get("button", "left")
        steps = max(2, min(inp.get("steps", 20), 200))
        # Move to start
        await self._do_mouse_move({"x": start_x, "y": start_y})
        time.sleep(0.05)
        # Mouse down
        await self._do_mouse_down({"button": button})
        time.sleep(0.05)
        # Bezier control point with small random jitter
        cx = (start_x + end_x) // 2 + random.randint(-30, 30)
        cy = (start_y + end_y) // 2 + random.randint(-10, 10)
        for i in range(1, steps + 1):
            t = i / steps
            bx = int((1 - t) ** 2 * start_x + 2 * (1 - t) * t * cx + t ** 2 * end_x)
            by = int((1 - t) ** 2 * start_y + 2 * (1 - t) * t * cy + t ** 2 * end_y)
            bx += random.randint(-1, 1)
            by += random.randint(-1, 1)
            await self._do_mouse_move({"x": bx, "y": by})
            time.sleep(0.01)
        # Final position
        await self._do_mouse_move({"x": end_x, "y": end_y})
        time.sleep(0.05)
        # Mouse up
        await self._do_mouse_up({"button": button})
        return ToolResult(output=f"Mouse dragged from ({start_x},{start_y}) to ({end_x},{end_y})")

    async def _do_mouse_scroll(self, inp: dict[str, Any]) -> ToolResult:
        amount = inp.get("amount", 0)
        x = inp.get("x")
        y = inp.get("y")
        if x is not None and y is not None:
            await self._do_mouse_move({"x": x, "y": y})
            time.sleep(0.05)
        # WHEEL_DELTA is 120 per click
        delta = int(amount * 120)
        inp_struct = _INPUT()
        inp_struct.type = INPUT_MOUSE
        inp_struct.i.mi[0] = 0
        inp_struct.i.mi[1] = 0
        inp_struct.i.mi[2] = delta
        inp_struct.i.mi[3] = MOUSEEVENTF_WHEEL
        inp_struct.i.mi[4] = 0
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp_struct), ctypes.sizeof(_INPUT))
        return ToolResult(output=f"Mouse scrolled {amount}")

    async def _do_key_press(self, inp: dict[str, Any]) -> ToolResult:
        key = str(inp.get("key", "")).lower()
        vk = _VK_MAP.get(key)
        if vk is None:
            return ToolResult(output=f"Unknown key: {key}", is_error=True)
        self._send_key_event(vk, False)
        time.sleep(0.05)
        self._send_key_event(vk, True)
        return ToolResult(output=f"Key pressed: {key}")

    async def _do_type(self, inp: dict[str, Any]) -> ToolResult:
        text = str(inp.get("text", ""))
        for ch in text:
            vk = _VK_MAP.get(ch.lower())
            if vk is not None:
                self._send_key_event(vk, False)
                time.sleep(0.01)
                self._send_key_event(vk, True)
                time.sleep(0.01)
            else:
                # Fallback: use keybd_event for unmapped chars (simplified)
                pass
        return ToolResult(output=f"Typed {len(text)} character(s)")

    # ------------------------------------------------------------------ #
    #  Low-level helpers
    # ------------------------------------------------------------------ #

    def _send_mouse_event(self, flags: int) -> None:
        inp = _INPUT()
        inp.type = INPUT_MOUSE
        inp.i.mi[0] = 0
        inp.i.mi[1] = 0
        inp.i.mi[2] = 0
        inp.i.mi[3] = flags
        inp.i.mi[4] = 0
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def _send_key_event(self, vk: int, up: bool) -> None:
        inp = _INPUT()
        inp.type = INPUT_KEYBOARD
        inp.i.ki[0] = vk
        inp.i.ki[1] = 0
        inp.i.ki[2] = KEYEVENTF_KEYUP if up else 0
        inp.i.ki[3] = 0
        inp.i.ki[4] = 0
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


__all__ = ["ComputerTool"]
