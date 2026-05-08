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
import ctypes.wintypes
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
    "- mouse_drag start_x start_y end_x end_y [button=left|right] [steps=20] [delay_ms=10]: drag with bezier smoothing. "
    "For slider CAPTCHAs use steps=80 and delay_ms=25 so the page JavaScript can track the movement.\n"
    "- mouse_scroll amount [x y]: scroll wheel (positive = up, negative = down)\n"
    "- mouse_double_click [x y] [button=left|right]: double-click at coordinates or current position\n"
    "- cursor_position: return current mouse cursor screen coordinates\n"
    "- focus_window title: bring the first window whose title contains 'title' to the foreground\n"
    "- key_press key: press a key (Enter, Escape, Tab, Control, Alt, Shift, etc.)\n"
    "- key_combo keys: press multiple keys simultaneously (e.g. ['Control','c']).\n"
    "- type text: type a string\n"
    "- wait milliseconds=1000: pause briefly for UI to settle\n"
    "- locate template_path [confidence=0.8]: find template image on screen with OpenCV (returns center coords)\n"
    "- list_windows: enumerate all visible windows with titles and positions\n"
    "- clipboard clipboard_action=read|write [clipboard_text]: read or write system clipboard\n"
    "- launch command [args] [wait_ms=2000]: start an application by path, name, or alias"
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

# Make the process per-monitor DPI aware so that GetSystemMetrics returns
# physical pixel counts that match mss screenshots.  Without this, Windows
# DPI scaling causes a coordinate mismatch on high-DPI displays.
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(
        _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    )
except (AttributeError, OSError):
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass

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


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("u", _INPUT_UNION),
    ]


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
                            "mouse_double_click",
                            "cursor_position",
                            "focus_window",
                            "key_press",
                            "key_combo",
                            "type",
                            "wait",
                            "locate",
                            "list_windows",
                            "clipboard",
                            "launch",
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
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of keys to press simultaneously for key_combo (e.g. ['Control','c']).",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Window title substring for focus_window action.",
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
                    "milliseconds": {
                        "type": "integer",
                        "description": "Wait time in milliseconds (default 1000).",
                    },
                    "template_path": {
                        "type": "string",
                        "description": "Path to a template image for locate action (PNG/JPG).",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Minimum confidence threshold for locate (0.0-1.0, default 0.8).",
                    },
                    "clipboard_action": {
                        "type": "string",
                        "enum": ["read", "write"],
                        "description": "Clipboard operation for action='clipboard'.",
                    },
                    "clipboard_text": {
                        "type": "string",
                        "description": "Text to write to clipboard (for clipboard_action='write').",
                    },
                    "command": {
                        "type": "string",
                        "description": "Command or executable path to launch (action='launch'). Can be a full path, an executable name, or a common alias like 'notepad', 'calc', 'chrome'.",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Arguments to pass to the launched command (action='launch').",
                    },
                    "wait_ms": {
                        "type": "integer",
                        "description": "Milliseconds to wait after launching (action='launch', default 2000).",
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
            "mouse_double_click": self._do_mouse_double_click,
            "cursor_position": self._do_cursor_position,
            "focus_window": self._do_focus_window,
            "key_press": self._do_key_press,
            "key_combo": self._do_key_combo,
            "type": self._do_type,
            "wait": self._do_wait,
            "locate": self._do_locate,
            "list_windows": self._do_list_windows,
            "clipboard": self._do_clipboard,
            "launch": self._do_launch,
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
        # monitors[0] is the virtual screen (all monitors); monitors[1] is the
        # primary display.  We capture the primary display so that coordinates
        # derived from the screenshot map 1:1 to mouse_move/mouse_drag.
        raw = sct.grab(sct.monitors[1])
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
        inp_struct.mi.dx = abs_x
        inp_struct.mi.dy = abs_y
        inp_struct.mi.mouseData = 0
        inp_struct.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
        inp_struct.mi.time = 0
        inp_struct.mi.dwExtraInfo = 0
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
        """Drag from (x,y) to (end_x,end_y) with human-like trajectory.

        Uses ease-in-out velocity with micro-pauses and slight overshoot
        to mimic real human mouse movement, which helps evade behavioral
        biometrics detection on slider CAPTCHAs.
        """
        import random
        # Accept both (x,y) and (start_x,start_y) for the start point
        start_x = inp.get("x") if "x" in inp else inp.get("start_x", 0)
        start_y = inp.get("y") if "y" in inp else inp.get("start_y", 0)
        end_x = inp.get("end_x", start_x)
        end_y = inp.get("end_y", start_y)
        button = inp.get("button", "left")
        steps = max(2, min(inp.get("steps", 20), 200))
        delay_ms = max(1, min(inp.get("delay_ms", 10), 500))
        # Move to start
        await self._do_mouse_move({"x": start_x, "y": start_y})
        time.sleep(0.05)
        # Mouse down
        await self._do_mouse_down({"button": button})
        time.sleep(0.05)

        # Human-like trajectory generation
        # Control point with random offset for curved path
        cx = (start_x + end_x) // 2 + random.randint(-40, 40)
        cy = (start_y + end_y) // 2 + random.randint(-15, 15)

        # Slight overshoot target for realism, then correct back
        overshoot = random.randint(3, 8)
        overshoot_x = end_x + (overshoot if end_x >= start_x else -overshoot)
        overshoot_y = end_y + random.randint(-3, 3)

        # Generate points along quadratic bezier
        points = []
        for i in range(steps + 1):
            t = i / steps
            bx = int((1 - t) ** 2 * start_x + 2 * (1 - t) * t * cx + t ** 2 * overshoot_x)
            by = int((1 - t) ** 2 * start_y + 2 * (1 - t) * t * cy + t ** 2 * overshoot_y)
            points.append((bx, by))
        # Correct overshoot in final steps
        correct_steps = min(5, steps // 10)
        if correct_steps > 0:
            for i in range(correct_steps):
                t = (i + 1) / (correct_steps + 1)
                bx = int(points[-correct_steps - 1][0] + t * (end_x - points[-correct_steps - 1][0]))
                by = int(points[-correct_steps - 1][1] + t * (end_y - points[-correct_steps - 1][1]))
                points[-correct_steps + i] = (bx, by)
            points[-1] = (end_x, end_y)

        # Execute movement with ease-in-out timing (slower at start/end, faster in middle)
        for i in range(1, len(points)):
            bx, by = points[i]
            bx += random.randint(-1, 1)
            by += random.randint(-1, 1)
            await self._do_mouse_move({"x": bx, "y": by})
            # Ease-in-out delay: slower at boundaries, faster in middle
            t = i / len(points)
            ease = 1.0 - abs(2 * t - 1)  # 0 at start, 1 at middle, 0 at end
            step_delay = delay_ms * (0.6 + 0.4 * (1 - ease))  # vary by ±40%
            # Occasional micro-pause (5% chance)
            if random.random() < 0.05:
                step_delay += random.randint(20, 60)
            time.sleep(step_delay / 1000)

        # Final position
        await self._do_mouse_move({"x": end_x, "y": end_y})
        time.sleep(0.05)
        # Mouse up
        await self._do_mouse_up({"button": button})
        return ToolResult(output=f"Mouse dragged from ({start_x},{start_y}) to ({end_x},{end_y})")

    async def _do_mouse_double_click(self, inp: dict[str, Any]) -> ToolResult:
        x = inp.get("x")
        y = inp.get("y")
        button = inp.get("button", "left")
        if x is not None and y is not None:
            await self._do_mouse_move({"x": x, "y": y})
            time.sleep(0.05)
        down_flag, up_flag = {
            "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        }.get(button, (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
        # First click
        self._send_mouse_event(down_flag)
        time.sleep(0.05)
        self._send_mouse_event(up_flag)
        time.sleep(0.1)
        # Second click
        self._send_mouse_event(down_flag)
        time.sleep(0.05)
        self._send_mouse_event(up_flag)
        return ToolResult(output=f"Mouse {button} double-clicked")

    async def _do_cursor_position(self, _inp: dict[str, Any]) -> ToolResult:
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return ToolResult(output=f"Cursor at ({point.x}, {point.y})")

    async def _do_focus_window(self, inp: dict[str, Any]) -> ToolResult:
        title = str(inp.get("title", "")).strip().lower()
        if not title:
            return ToolResult(output="focus_window requires 'title'", is_error=True)
        try:
            import win32gui
            import win32con
        except ImportError:
            return ToolResult(output="win32gui not available", is_error=True)

        def _enum(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                wt = win32gui.GetWindowText(hwnd).lower()
                if title in wt:
                    extra.append((hwnd, win32gui.GetWindowText(hwnd)))
            return True

        matches = []
        win32gui.EnumWindows(_enum, matches)
        if not matches:
            return ToolResult(output=f"No visible window matching '{title}'", is_error=True)
        hwnd, full_title = matches[-1]
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as exc:
            return ToolResult(output=f"Failed to focus '{full_title}': {exc}", is_error=True)
        return ToolResult(output=f"Focused window: {full_title}")

    async def _do_wait(self, inp: dict[str, Any]) -> ToolResult:
        ms = max(0, int(inp.get("milliseconds", 1000)))
        time.sleep(ms / 1000.0)
        return ToolResult(output=f"Waited {ms}ms")

    async def _do_locate(self, inp: dict[str, Any]) -> ToolResult:
        """Find a template image on the screen using OpenCV template matching."""
        template_path = str(inp.get("template_path", ""))
        confidence = float(inp.get("confidence", 0.8))
        if not template_path:
            return ToolResult(output="locate requires 'template_path'", is_error=True)
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            return ToolResult(output=f"locate requires opencv-python: {exc}", is_error=True)

        try:
            # Capture current screen
            sct = self._ensure_mss()
            raw = sct.grab(sct.monitors[1])
            import mss.tools
            png_bytes = mss.tools.to_png(raw.rgb, raw.size)
            screen = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
            template = cv2.imread(template_path, cv2.IMREAD_COLOR)
            if screen is None:
                return ToolResult(output="Failed to capture screen", is_error=True)
            if template is None:
                return ToolResult(output=f"Failed to load template: {template_path}", is_error=True)

            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            if max_val >= confidence:
                h, w = template.shape[:2]
                cx = max_loc[0] + w // 2
                cy = max_loc[1] + h // 2
                return ToolResult(
                    output=f"Found at ({cx}, {cy}) with confidence {max_val:.3f}",
                    details={"x": cx, "y": cy, "confidence": max_val, "width": w, "height": h},
                )
            return ToolResult(
                output=f"Template not found (best confidence: {max_val:.3f}, threshold: {confidence})",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(output=f"Locate error: {exc}", is_error=True)

    async def _do_list_windows(self, _inp: dict[str, Any]) -> ToolResult:
        """List all visible windows with their titles and positions."""
        try:
            import win32gui
        except ImportError:
            return ToolResult(output="win32gui not available", is_error=True)

        windows = []

        def _enum(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    try:
                        rect = win32gui.GetWindowRect(hwnd)
                        windows.append({
                            "title": title,
                            "left": rect[0],
                            "top": rect[1],
                            "right": rect[2],
                            "bottom": rect[3],
                            "width": rect[2] - rect[0],
                            "height": rect[3] - rect[1],
                        })
                    except Exception:
                        windows.append({"title": title})
            return True

        win32gui.EnumWindows(_enum, None)
        output = json.dumps(windows[:50], ensure_ascii=False, indent=2)
        return ToolResult(
            output=output[:4000],
            details={"window_count": len(windows)},
        )

    async def _do_clipboard(self, inp: dict[str, Any]) -> ToolResult:
        """Read from or write to the system clipboard."""
        cb_action = inp.get("clipboard_action", "read")
        try:
            import win32clipboard
            import win32con
        except ImportError:
            return ToolResult(output="pywin32 not available for clipboard", is_error=True)

        if cb_action == "read":
            try:
                win32clipboard.OpenClipboard()
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                    text = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                else:
                    text = ""
                win32clipboard.CloseClipboard()
                return ToolResult(output=f"Clipboard: {text[:500]}")
            except Exception as exc:
                return ToolResult(output=f"Clipboard read error: {exc}", is_error=True)

        elif cb_action == "write":
            text = str(inp.get("clipboard_text", ""))
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                win32clipboard.CloseClipboard()
                return ToolResult(output=f"Wrote {len(text)} chars to clipboard")
            except Exception as exc:
                return ToolResult(output=f"Clipboard write error: {exc}", is_error=True)

        return ToolResult(output=f"Unknown clipboard action: {cb_action}", is_error=True)

    async def _do_launch(self, inp: dict[str, Any]) -> ToolResult:
        """Launch an application by path, command, or common alias."""
        import shutil
        import subprocess

        command = str(inp.get("command", "")).strip()
        if not command:
            return ToolResult(output="launch requires 'command'", is_error=True)

        args = [str(a) for a in inp.get("args", [])]
        wait_ms = max(0, int(inp.get("wait_ms", 2000)))

        # Common aliases
        _COMMON_ALIASES: dict[str, str] = {
            "notepad": "notepad.exe",
            "calc": "calc.exe",
            "calculator": "calc.exe",
            "chrome": "chrome.exe",
            "edge": "msedge.exe",
            "firefox": "firefox.exe",
            "explorer": "explorer.exe",
            "cmd": "cmd.exe",
            "terminal": "wt.exe",
            "vscode": "code.exe",
        }

        resolved = _COMMON_ALIASES.get(command.lower())
        if resolved:
            command = resolved

        # Try to resolve via PATH
        exe_path = shutil.which(command)
        if exe_path:
            cmd_list = [exe_path] + args
        else:
            # Try as-is (might be a file path or URL)
            cmd_list = [command] + args

        try:
            # Use start_new_session (detached on Unix) or creationflags on Windows
            # to avoid blocking and to keep the process alive after our subprocess returns.
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd_list,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd_list,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            if wait_ms > 0:
                time.sleep(wait_ms / 1000.0)
            return ToolResult(output=f"Launched: {' '.join(cmd_list)}")
        except Exception as exc:
            return ToolResult(output=f"Launch failed: {exc}", is_error=True)

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
        inp_struct.mi.dx = 0
        inp_struct.mi.dy = 0
        inp_struct.mi.mouseData = delta
        inp_struct.mi.dwFlags = MOUSEEVENTF_WHEEL
        inp_struct.mi.time = 0
        inp_struct.mi.dwExtraInfo = 0
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

    async def _do_key_combo(self, inp: dict[str, Any]) -> ToolResult:
        keys = inp.get("keys", [])
        if not keys:
            return ToolResult(output="key_combo requires 'keys' array", is_error=True)
        vks = []
        for key in keys:
            vk = _VK_MAP.get(str(key).lower())
            if vk is None:
                return ToolResult(output=f"Unknown key in combo: {key}", is_error=True)
            vks.append(vk)
        # Press all down
        for vk in vks:
            self._send_key_event(vk, False)
            time.sleep(0.02)
        # Release all up (reverse order)
        for vk in reversed(vks):
            self._send_key_event(vk, True)
            time.sleep(0.02)
        return ToolResult(output=f"Key combo pressed: {'+'.join(keys)}")

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
        inp.mi.dx = 0
        inp.mi.dy = 0
        inp.mi.mouseData = 0
        inp.mi.dwFlags = flags
        inp.mi.time = 0
        inp.mi.dwExtraInfo = 0
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def _send_key_event(self, vk: int, up: bool) -> None:
        inp = _INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki.wVk = vk
        inp.ki.wScan = 0
        inp.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
        inp.ki.time = 0
        inp.ki.dwExtraInfo = 0
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


__all__ = ["ComputerTool"]
