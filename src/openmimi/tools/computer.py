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

import asyncio
import base64
import ctypes
import ctypes.wintypes
import io
import json
import os
import subprocess
import sys
import time
from typing import Any

from ..utils.env_flags import screenshots_disabled
from .base import ToolBase
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Control the Windows desktop via screenshot + input injection. "
    "All coordinates are absolute screen pixels. "
    "After each action a fresh screenshot is returned so the model can observe state.\n\n"
    "Action reference:\n"
    "- screenshot: capture the entire desktop\n"
    "- mouse_move x y [humanize=false] [steps=20] [delay_ms=10]: move cursor to (x, y). Set humanize=true for human-like Bezier trajectory with acceleration/deceleration.\n"
    "- mouse_click [x y] [button=left|right] [wander=false] [width] [height]: click at coordinates or current position. Set wander=true for human-like micro-movements near target before clicking. Provide width/height to randomise the hit point within the element bounds instead of the exact centre.\n"
    "- mouse_down [button=left|right]: press and hold mouse button at current position\n"
    "- mouse_up [button=left|right]: release mouse button at current position\n"
    "- mouse_drag start_x start_y end_x end_y [button=left|right] [steps=20] [delay_ms=10] [humanize=true]: drag with quadratic-Bezier smoothing and acceleration/deceleration. "
    "For slider CAPTCHAs use steps=80 and delay_ms=25 so the page JavaScript can track the movement.\n"
    "- mouse_scroll amount [x y] [smooth=true]: scroll wheel (positive = up, negative = down). Large scrolls are broken into small random steps by default.\n"
    "- mouse_double_click [x y] [button=left|right] [wander=false] [width] [height]: double-click at coordinates or current position. Set wander=true for human-like micro-movements before clicking. Provide width/height to randomise the hit point within the element bounds.\n"
    "- cursor_position: return current mouse cursor screen coordinates\n"
    "- focus_window title: bring the first window whose title contains 'title' to the foreground\n"
    "- key_press key: press a key (Enter, Escape, Tab, Control, Alt, Shift, etc.)\n"
    "- key_combo keys: press multiple keys simultaneously (e.g. ['Control','c']).\n"
    "- type text: type a string\n"
    "- wait milliseconds=1000: pause briefly for UI to settle\n"
    "- locate template_path [confidence=0.8] [scales]: find template image on screen with multi-scale OpenCV matching (returns center coords). Searches 0.5x-1.5x by default; optionally pass a custom scales array.\n"
    "- click_image template_path [confidence=0.8] [button=left|right]: find template image on screen and click a random point inside its bounds. "
    "Uses multi-scale matching so slight UI zoom differences don't break the match. Useful for clicking icons, buttons, or UI elements in native apps when coordinates are unknown.\n"
    "- list_windows: enumerate all visible windows with titles and positions\n"
    "- clipboard clipboard_action=read|write [clipboard_text]: read or write system clipboard\n"
    "- launch command [args] [wait_ms=2000]: start an application by path, name, or alias\n"
    "- file file_action=read|write file_path [file_content]: read from or write to a file on disk.\n"
    "- get_screen_info: return primary monitor resolution and DPI.\n"
    "- ocr [x y width height] [language=chi_sim+eng]: extract text from a screen region using Tesseract OCR. "
    "  If no region is specified, OCR the full screenshot. Useful for reading native app UI or notifications.\n"
    "- click_text target_text [button=left|right] [language=chi_sim+eng]: find text on screen via OCR and click it. "
    "  Useful for interacting with native apps where you cannot use coordinates.\n"
    "- window_manage title window_action [x y width height]: manage windows by title substring. "
    "  Actions: move, resize, minimize, maximize, restore, close.\n"
    "- shell command [timeout=30]: execute a shell command and return stdout/stderr (use with care).\n"
    "- batch steps [bail=true]: execute multiple actions in one call. Each item in 'steps' is an object with 'action' and its parameters. Stops on first error unless bail=false."
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

    def __init__(
        self,
        screen_dir: str | None = None,
        screenshot_scale: float = 1.0,
        screenshot_quality: int = 75,
        screenshot_format: str = "jpeg",
    ) -> None:
        self._screen_dir = screen_dir or os.path.join("data", "screens")
        os.makedirs(self._screen_dir, exist_ok=True)
        self._screenshot_scale = max(0.1, min(1.0, float(screenshot_scale)))
        self._screenshot_quality = max(1, min(95, int(screenshot_quality)))
        self._screenshot_format = "jpeg" if screenshot_format.lower() in ("jpeg", "jpg") else "png"
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
                            "click_image",
                            "list_windows",
                            "clipboard",
                            "launch",
                            "file",
                            "get_screen_info",
                            "ocr",
                            "click_text",
                            "window_manage",
                            "shell",
                            "batch",
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
                    "wander": {
                        "type": "boolean",
                        "description": "For mouse_click / mouse_double_click: make small random micro-movements near target before clicking to simulate human aiming (default false).",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Element width in pixels. When provided with height, click is randomly offset within the element bounds instead of the exact center.",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Element height in pixels. When provided with width, click is randomly offset within the element bounds instead of the exact center.",
                    },
                    "smooth": {
                        "type": "boolean",
                        "description": "For mouse_scroll: break large scrolls into smaller random steps to mimic human wheel use (default true).",
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
                    "region": {
                        "type": "object",
                        "description": "Optional sub-rectangle for screenshot action: {left, top, width, height} in absolute screen pixels. If omitted, captures the full primary monitor. Use the box returned by list_windows / focus_window to crop to a specific window.",
                        "properties": {
                            "left": {"type": "integer"},
                            "top": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
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
                        "description": "Number of intermediate points for drag smoothing or humanized mouse_move (default 20).",
                    },
                    "delay_ms": {
                        "type": "integer",
                        "description": "Delay between steps in milliseconds for drag or humanized mouse_move (default 10).",
                    },
                    "humanize": {
                        "type": "boolean",
                        "description": "Use human-like Bezier trajectory for mouse_move instead of instant jump (default false).",
                    },
                    "milliseconds": {
                        "type": "integer",
                        "description": "Wait time in milliseconds (default 1000).",
                    },
                    "template_path": {
                        "type": "string",
                        "description": "Path to a template image for locate or click_image action (PNG/JPG).",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Minimum confidence threshold for locate/click_image (0.0-1.0, default 0.8). Multi-scale search is used automatically.",
                    },
                    "scales": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Optional custom scale factors for locate/click_image template matching (default: 0.5 to 1.5 in 0.1 steps).",
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
                        "description": "Command or executable path to launch or execute (action='launch' or 'shell'). For launch: can be a full path, executable name, or alias like 'notepad', 'calc', 'chrome'. For shell: a shell command string to execute.",
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
                    "file_action": {
                        "type": "string",
                        "enum": ["read", "write"],
                        "description": "File operation for action='file' (read or write).",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file for action='file'.",
                    },
                    "file_content": {
                        "type": "string",
                        "description": "Content to write for action='file' with file_action='write'.",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Region width for action='ocr' (default: full screen width).",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Region height for action='ocr' (default: full screen height).",
                    },
                    "language": {
                        "type": "string",
                        "description": "Tesseract language code(s) for action='ocr' (default: chi_sim+eng).",
                    },
                    "target_text": {
                        "type": "string",
                        "description": "Text to find on screen and click for action='click_text'.",
                    },
                    "window_action": {
                        "type": "string",
                        "enum": ["move", "resize", "minimize", "maximize", "restore", "close"],
                        "description": "Window operation for action='window_manage'.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of action objects for action='batch'. Each object should have an 'action' key and the corresponding parameters.",
                    },
                    "bail": {
                        "type": "boolean",
                        "description": "For action='batch', stop on first error (default true).",
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
            "click_image": self._do_click_image,
            "list_windows": self._do_list_windows,
            "clipboard": self._do_clipboard,
            "launch": self._do_launch,
            "file": self._do_file,
            "get_screen_info": self._do_get_screen_info,
            "ocr": self._do_ocr,
            "click_text": self._do_click_text,
            "window_manage": self._do_window_manage,
            "shell": self._do_shell,
            "batch": self._do_batch,
        }
        handler = handlers.get(action)
        if handler is None:
            return ToolResult(output=f"Unknown computer action: {action}", is_error=True)
        return await handler(inp)

    # ------------------------------------------------------------------ #
    #  Actions
    # ------------------------------------------------------------------ #

    async def _do_screenshot(self, inp: dict[str, Any]) -> ToolResult:
        if screenshots_disabled():
            return ToolResult(
                output="Screenshots disabled by default. Set OPENMIMI_ENABLE_SCREENSHOTS=1 or pass --screenshots to enable.",
                base64_image=None,
            )
        sct = self._ensure_mss()
        region = inp.get("region")
        grab_region: dict[str, int] | None = None
        if isinstance(region, dict):
            def _as_int(v: Any) -> int:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return 0
            grab_region = {
                "left": _as_int(region.get("left", 0)),
                "top": _as_int(region.get("top", 0)),
                "width": _as_int(region.get("width", 0)),
                "height": _as_int(region.get("height", 0)),
            }
            if grab_region["width"] <= 0 or grab_region["height"] <= 0:
                return ToolResult(
                    output=(
                        f"Screenshot region needs positive width and height, "
                        f"got {grab_region}"
                    ),
                    is_error=True,
                )
            raw = sct.grab(grab_region)
        else:
            # monitors[0] is the virtual screen (all monitors); monitors[1] is the
            # primary display.  We capture the primary display so that coordinates
            # derived from the screenshot map 1:1 to mouse_move/mouse_drag.
            raw = sct.grab(sct.monitors[1])
        import mss.tools
        img_bytes = mss.tools.to_png(raw.rgb, raw.size)
        media_type = "image/png"
        ext = "png"

        # Scale and/or convert to JPEG to save LLM tokens and bandwidth
        if self._screenshot_scale < 1.0 or self._screenshot_format == "jpeg":
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(img_bytes))
                if self._screenshot_scale < 1.0:
                    new_size = (
                        max(1, int(img.width * self._screenshot_scale)),
                        max(1, int(img.height * self._screenshot_scale)),
                    )
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                if self._screenshot_format == "jpeg":
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    img.save(buf, format="JPEG", quality=self._screenshot_quality, optimize=True)
                    media_type = "image/jpeg"
                    ext = "jpg"
                else:
                    img.save(buf, format="PNG", optimize=True)
                img_bytes = buf.getvalue()
            except Exception:
                pass  # Fall back to original if PIL fails

        b64 = base64.b64encode(img_bytes).decode("ascii")
        path = os.path.join(
            self._screen_dir, f"screen_{int(time.time() * 1000)}.{ext}"
        )
        with open(path, "wb") as f:
            f.write(img_bytes)
        region_suffix = (
            f" region=({grab_region['left']},{grab_region['top']})"
            if grab_region
            else ""
        )
        return ToolResult(
            output=f"Screenshot saved to {path} ({raw.width}x{raw.height}){region_suffix}",
            base64_image=f"data:{media_type};base64,{b64}",
            image_media_type=media_type,
        )

    async def _do_mouse_move(self, inp: dict[str, Any]) -> ToolResult:
        x = inp.get("x", 0)
        y = inp.get("y", 0)
        humanize = inp.get("humanize", False)

        if humanize:
            steps = max(2, min(inp.get("steps", 20), 200))
            delay_ms = max(1, min(inp.get("delay_ms", 10), 500))
            point = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            track = self._generate_human_trajectory(point.x, point.y, x, y, steps, delay_ms)
            await self._execute_human_movement(track, delay_ms)
            return ToolResult(output=f"Mouse human-moved to ({x}, {y}) via {len(track)} points")

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

    def _jittered_click_coords(
        self, cx: int, cy: int, width: int = 0, height: int = 0, max_offset: int = 5
    ) -> tuple[int, int]:
        """Return a click point randomly offset from center within the element bounds.

        If width/height are known, the offset is constrained so the point stays
        inside the bounding box (with a 2-px margin).  Otherwise a small
        ±max_offset gaussian jitter is applied.
        """
        import random
        if width > 4 and height > 4:
            margin = 2
            half_w = width // 2 - margin
            half_h = height // 2 - margin
            ox = random.randint(-max(1, half_w), max(1, half_w))
            oy = random.randint(-max(1, half_h), max(1, half_h))
            return cx + ox, cy + oy
        ox = int(random.gauss(0, max_offset / 2))
        oy = int(random.gauss(0, max_offset / 2))
        return cx + ox, cy + oy

    async def _do_mouse_click(self, inp: dict[str, Any]) -> ToolResult:
        import random
        x = inp.get("x")
        y = inp.get("y")
        button = inp.get("button", "left")
        wander = inp.get("wander", False)
        width = int(inp.get("width", 0))
        height = int(inp.get("height", 0))
        # Move first if coordinates provided
        if x is not None and y is not None:
            tx, ty = self._jittered_click_coords(x, y, width, height)
            await self._do_mouse_move({"x": tx, "y": ty})
            time.sleep(0.05)
            if wander:
                for _ in range(random.randint(2, 4)):
                    wx = tx + random.randint(-8, 8)
                    wy = ty + random.randint(-8, 8)
                    await self._do_mouse_move({"x": wx, "y": wy})
                    time.sleep(random.uniform(0.03, 0.12))
                await self._do_mouse_move({"x": tx, "y": ty})
                time.sleep(random.uniform(0.05, 0.15))
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

    def _generate_human_trajectory(
        self, start_x: int, start_y: int, end_x: int, end_y: int, steps: int, delay_ms: int
    ) -> list[tuple[int, int]]:
        """Generate a human-like quadratic-Bezier trajectory with acceleration/deceleration.

        Uses a control point offset perpendicular to the straight-line path so the
        cursor arcs slightly (mimicking arm kinematics).  Velocity ramps up for
        ~80 % of the distance then decelerates, with random micro-jitter and
        occasional hesitations.
        """
        import random
        import math

        dx = end_x - start_x
        dy = end_y - start_y
        distance = math.hypot(dx, dy)
        if distance < 1:
            return [(end_x, end_y)]

        # Perpendicular offset for the control point (arc direction)
        # Offset magnitude is 5-15 % of distance, capped at 80 px
        offset_mag = min(80, distance * random.uniform(0.05, 0.15))
        # Randomly choose left or right of the straight path
        side = 1 if random.random() < 0.5 else -1
        # Perpendicular unit vector (-dy, dx) / distance
        perp_x = -dy / distance * offset_mag * side
        perp_y = dx / distance * offset_mag * side
        ctrl_x = (start_x + end_x) / 2 + perp_x
        ctrl_y = (start_y + end_y) / 2 + perp_y

        # Adaptive step count: short moves don't need many points
        steps = max(3, min(steps, int(distance / 3) + 3))

        track: list[tuple[int, int]] = []
        # Velocity profile: accelerate for 0-0.7, cruise 0.7-0.85, decelerate 0.85-1.0
        for i in range(steps + 1):
            t = i / steps
            # Ease-in-out cubic: starts slow, accelerates, decelerates at end
            t_eased = t * t * (3 - 2 * t)
            # Quadratic Bezier
            bx = int((1 - t_eased) ** 2 * start_x + 2 * (1 - t_eased) * t_eased * ctrl_x + t_eased ** 2 * end_x)
            by = int((1 - t_eased) ** 2 * start_y + 2 * (1 - t_eased) * t_eased * ctrl_y + t_eased ** 2 * end_y)
            # Micro-jitter perpendicular to path
            jitter = random.randint(-1, 1)
            jx = int(bx + jitter * perp_x / offset_mag) if offset_mag > 0 else bx
            jy = int(by + jitter * perp_y / offset_mag) if offset_mag > 0 else by
            track.append((jx, jy))

        # Deduplicate consecutive identical points
        deduped: list[tuple[int, int]] = [track[0]]
        for pt in track[1:]:
            if pt != deduped[-1]:
                deduped.append(pt)

        if not deduped or deduped[-1] != (end_x, end_y):
            deduped.append((end_x, end_y))
        return deduped

    async def _execute_human_movement(
        self, track: list[tuple[int, int]], delay_ms: int
    ) -> None:
        """Execute a generated trajectory with randomized timing."""
        import random
        for bx, by in track:
            await self._do_mouse_move({"x": bx, "y": by})
            step_delay = delay_ms * random.uniform(0.7, 1.3)
            if random.random() < 0.05:
                step_delay += random.randint(20, 60)
            time.sleep(step_delay / 1000)
        # Small random wiggle near target
        for _ in range(random.randint(0, 2)):
            wiggle_x = track[-1][0] + random.randint(-2, 2)
            wiggle_y = track[-1][1] + random.randint(-1, 1)
            await self._do_mouse_move({"x": wiggle_x, "y": wiggle_y})
            time.sleep(random.uniform(0.05, 0.15))

    async def _do_mouse_drag(self, inp: dict[str, Any]) -> ToolResult:
        """Drag from (x,y) to (end_x,end_y) with human-like trajectory.

        Uses physics-based acceleration/deceleration (accelerate for ~80% of
        distance, decelerate for remaining ~20%) with random noise and micro-
        pauses.  This mimics real human arm movement and helps evade
        behavioural biometrics on slider CAPTCHAs.
        """
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

        distance = ((end_x - start_x) ** 2 + (end_y - start_y) ** 2) ** 0.5
        if distance < 1:
            await self._do_mouse_up({"button": button})
            return ToolResult(output=f"Mouse dragged from ({start_x},{start_y}) to ({end_x},{end_y})")

        track = self._generate_human_trajectory(start_x, start_y, end_x, end_y, steps, delay_ms)
        await self._execute_human_movement(track, delay_ms)

        # Final position
        await self._do_mouse_move({"x": end_x, "y": end_y})
        time.sleep(0.05)
        # Mouse up
        await self._do_mouse_up({"button": button})
        return ToolResult(output=f"Mouse dragged from ({start_x},{start_y}) to ({end_x},{end_y})")

    async def _do_mouse_double_click(self, inp: dict[str, Any]) -> ToolResult:
        import random
        x = inp.get("x")
        y = inp.get("y")
        button = inp.get("button", "left")
        wander = inp.get("wander", False)
        width = int(inp.get("width", 0))
        height = int(inp.get("height", 0))
        if x is not None and y is not None:
            tx, ty = self._jittered_click_coords(x, y, width, height)
            await self._do_mouse_move({"x": tx, "y": ty})
            time.sleep(0.05)
            if wander:
                for _ in range(random.randint(2, 4)):
                    wx = tx + random.randint(-8, 8)
                    wy = ty + random.randint(-8, 8)
                    await self._do_mouse_move({"x": wx, "y": wy})
                    time.sleep(random.uniform(0.03, 0.12))
                await self._do_mouse_move({"x": tx, "y": ty})
                time.sleep(random.uniform(0.05, 0.15))
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
            rect = win32gui.GetWindowRect(hwnd)
            info = {
                "title": full_title,
                "left": rect[0],
                "top": rect[1],
                "right": rect[2],
                "bottom": rect[3],
                "width": rect[2] - rect[0],
                "height": rect[3] - rect[1],
            }
        except Exception as exc:
            return ToolResult(output=f"Failed to focus '{full_title}': {exc}", is_error=True)
        return ToolResult(
            output=f"Focused window: {full_title} at ({info['left']},{info['top']}) size {info['width']}x{info['height']}",
            details=info,
        )

    async def _do_wait(self, inp: dict[str, Any]) -> ToolResult:
        ms = max(0, int(inp.get("milliseconds", 1000)))
        time.sleep(ms / 1000.0)
        return ToolResult(output=f"Waited {ms}ms")

    def _match_template_multiscale(
        self,
        screen: Any,
        template: Any,
        confidence: float,
        scales: list[float] | None = None,
    ) -> dict[str, Any] | None:
        """Multi-scale template matching with a Gaussian-pyramid-like search.

        Searches across 0.5x-1.5x template scales (customisable via ``scales``)
        so that slight UI zoom / DPI differences don't break a match.
        Returns the best match dict or None if nothing exceeds ``confidence``.
        """
        import cv2
        import numpy as np

        if scales is None:
            scales = [round(s, 2) for s in np.arange(0.5, 1.55, 0.1)]

        best_match = None
        best_val = -1.0

        for scale in scales:
            if scale <= 0:
                continue
            resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC)
            th, tw = resized.shape[:2]
            sh, sw = screen.shape[:2]
            if th > sh or tw > sw:
                continue
            result = cv2.matchTemplate(screen, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_val:
                best_val = max_val
                best_match = {
                    "x": max_loc[0] + tw // 2,
                    "y": max_loc[1] + th // 2,
                    "confidence": max_val,
                    "width": tw,
                    "height": th,
                    "scale": scale,
                }

        if best_match and best_match["confidence"] >= confidence:
            return best_match
        return None

    async def _do_locate(self, inp: dict[str, Any]) -> ToolResult:
        """Find a template image on the screen using multi-scale OpenCV template matching."""
        template_path = str(inp.get("template_path", ""))
        confidence = float(inp.get("confidence", 0.8))
        scales_raw = inp.get("scales")
        scales = None
        if isinstance(scales_raw, list) and scales_raw:
            scales = [float(s) for s in scales_raw if isinstance(s, (int, float)) and float(s) > 0]
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

            match = self._match_template_multiscale(screen, template, confidence, scales=scales)
            if match is not None:
                return ToolResult(
                    output=f"Found at ({match['x']}, {match['y']}) with confidence {match['confidence']:.3f} (scale={match['scale']})",
                    details=match,
                )
            return ToolResult(
                output=f"Template not found across scales 0.5x-1.5x (best confidence below {confidence})",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(output=f"Locate error: {exc}", is_error=True)

    async def _do_click_image(self, inp: dict[str, Any]) -> ToolResult:
        """Find a template image on the screen and click its center."""
        template_path = str(inp.get("template_path", ""))
        confidence = float(inp.get("confidence", 0.8))
        button = str(inp.get("button", "left")).lower()
        scales_raw = inp.get("scales")
        scales = None
        if isinstance(scales_raw, list) and scales_raw:
            scales = [float(s) for s in scales_raw if isinstance(s, (int, float)) and float(s) > 0]
        if not template_path:
            return ToolResult(output="click_image requires 'template_path'", is_error=True)
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            return ToolResult(output=f"click_image requires opencv-python: {exc}", is_error=True)

        try:
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

            match = self._match_template_multiscale(screen, template, confidence, scales=scales)
            if match is None:
                return ToolResult(
                    output=f"Template not found across scales 0.5x-1.5x (threshold: {confidence})",
                    is_error=True,
                )

            cx = match["x"]
            cy = match["y"]
            tx, ty = self._jittered_click_coords(cx, cy, match.get("width", 0), match.get("height", 0))
            await self._do_mouse_move({"x": tx, "y": ty})
            time.sleep(0.05)
            down_flag, up_flag = {
                "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
                "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
                "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
            }.get(button, (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
            self._send_mouse_event(down_flag)
            time.sleep(0.05)
            self._send_mouse_event(up_flag)
            return ToolResult(
                output=f"Clicked template at ({tx}, {ty}) with confidence {match['confidence']:.3f} (scale={match['scale']})",
                details={**match, "click_x": tx, "click_y": ty},
            )
        except Exception as exc:
            return ToolResult(output=f"click_image error: {exc}", is_error=True)

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

    async def _do_file(self, inp: dict[str, Any]) -> ToolResult:
        """Read from or write to a file on disk."""
        file_action = inp.get("file_action", "read")
        file_path = str(inp.get("file_path", ""))
        if not file_path:
            return ToolResult(output="file requires 'file_path'", is_error=True)
        try:
            if file_action == "read":
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                # Truncate very large files
                if len(content) > 8000:
                    content = content[:8000] + "\n... [truncated]"
                return ToolResult(
                    output=f"File: {file_path}\n```\n{content}\n```",
                    details={"path": file_path, "size": len(content)},
                )
            elif file_action == "write":
                file_content = str(inp.get("file_content", ""))
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(file_content)
                return ToolResult(
                    output=f"Wrote {len(file_content)} bytes to {file_path}",
                    details={"path": file_path, "bytes_written": len(file_content)},
                )
            else:
                return ToolResult(output=f"Unknown file_action: {file_action}", is_error=True)
        except Exception as exc:
            return ToolResult(output=f"File operation failed: {exc}", is_error=True)

    async def _do_mouse_scroll(self, inp: dict[str, Any]) -> ToolResult:
        import random
        amount = int(inp.get("amount", 0))
        x = inp.get("x")
        y = inp.get("y")
        smooth = inp.get("smooth", True)
        if x is not None and y is not None:
            await self._do_mouse_move({"x": x, "y": y})
            time.sleep(0.05)
        if amount == 0:
            return ToolResult(output="Mouse scrolled 0")

        # WHEEL_DELTA is 120 per click
        if smooth and abs(amount) > 3:
            # Break large scrolls into smaller random steps to mimic human wheel use
            remaining = amount
            step_sign = 1 if amount > 0 else -1
            while remaining != 0:
                step = random.randint(1, 3) * step_sign
                if abs(step) > abs(remaining):
                    step = remaining
                delta = int(step * 120)
                inp_struct = _INPUT()
                inp_struct.type = INPUT_MOUSE
                inp_struct.mi.dx = 0
                inp_struct.mi.dy = 0
                inp_struct.mi.mouseData = delta
                inp_struct.mi.dwFlags = MOUSEEVENTF_WHEEL
                inp_struct.mi.time = 0
                inp_struct.mi.dwExtraInfo = 0
                ctypes.windll.user32.SendInput(1, ctypes.byref(inp_struct), ctypes.sizeof(_INPUT))
                remaining -= step
                time.sleep(random.uniform(0.05, 0.15))
        else:
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
        # Check if all chars are ASCII and have VK mappings
        all_ascii = all(ord(ch) < 128 for ch in text)
        all_mappable = all(_VK_MAP.get(ch.lower()) is not None for ch in text if ch.strip())
        if all_ascii and all_mappable:
            for ch in text:
                vk = _VK_MAP.get(ch.lower())
                if vk is not None:
                    self._send_key_event(vk, False)
                    time.sleep(0.01)
                    self._send_key_event(vk, True)
                    time.sleep(0.01)
            return ToolResult(output=f"Typed {len(text)} character(s)")
        # For Unicode / non-mappable text, use clipboard paste
        try:
            import win32clipboard
            import win32con
        except ImportError:
            return ToolResult(output="pywin32 required for Unicode typing", is_error=True)
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            win32clipboard.CloseClipboard()
            # Ctrl+V paste
            self._send_key_event(_VK_MAP["control"], False)
            time.sleep(0.02)
            self._send_key_event(_VK_MAP["v"], False)
            time.sleep(0.02)
            self._send_key_event(_VK_MAP["v"], True)
            time.sleep(0.02)
            self._send_key_event(_VK_MAP["control"], True)
            time.sleep(0.05)
            return ToolResult(output=f"Typed {len(text)} character(s) via clipboard paste")
        except Exception as exc:
            return ToolResult(output=f"Unicode typing failed: {exc}", is_error=True)

    async def _do_get_screen_info(self, _inp: dict[str, Any]) -> ToolResult:
        """Return primary screen resolution and DPI information."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            width = user32.GetSystemMetrics(0)
            height = user32.GetSystemMetrics(1)
            # Try to get DPI
            try:
                dpi = user32.GetDpiForSystem()
            except Exception:
                dpi = 96
            return ToolResult(
                output=f"Screen: {width}x{height} @ {dpi} DPI",
                details={"width": width, "height": height, "dpi": dpi},
            )
        except Exception as exc:
            return ToolResult(output=f"get_screen_info failed: {exc}", is_error=True)

    async def _do_ocr(self, inp: dict[str, Any]) -> ToolResult:
        """Extract text from a screen region using Tesseract OCR with per-word confidence."""
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            return ToolResult(
                output="OCR requires pytesseract and Pillow. Install with: pip install pytesseract Pillow",
                is_error=True,
            )

        x = inp.get("x", 0)
        y = inp.get("y", 0)
        width = inp.get("width")
        height = inp.get("height")
        language = str(inp.get("language", "chi_sim+eng"))

        try:
            sct = self._ensure_mss()
            monitor = sct.monitors[1]
            # If region specified, crop; otherwise full screen
            if width is not None and height is not None:
                region = {
                    "left": max(0, x),
                    "top": max(0, y),
                    "width": min(width, monitor["width"] - x),
                    "height": min(height, monitor["height"] - y),
                }
            else:
                region = monitor

            raw = sct.grab(region)
            import mss.tools
            png_bytes = mss.tools.to_png(raw.rgb, raw.size)
            img = Image.open(io.BytesIO(png_bytes))
            # Tesseract expects RGB
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Use image_to_data for per-word confidence
            data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)
            words: list[str] = []
            confidences: list[int] = []
            for i, text in enumerate(data["text"]):
                word = str(text).strip()
                if not word:
                    continue
                conf = int(data["conf"][i]) if str(data["conf"][i]).lstrip("-").isdigit() else -1
                words.append(word)
                confidences.append(conf)

            full_text = " ".join(words)
            avg_conf = sum(c for c in confidences if c >= 0) / max(1, len([c for c in confidences if c >= 0]))

            return ToolResult(
                output=(
                    f"OCR result ({region['width']}x{region['height']} @{region['left']},{region['top']}) "
                    f"avg_conf={avg_conf:.1f}:\n{full_text[:2000]}"
                ),
                details={
                    "text": full_text,
                    "words": words,
                    "confidences": confidences,
                    "avg_confidence": avg_conf,
                    "region": region,
                    "language": language,
                },
            )
        except Exception as exc:
            return ToolResult(output=f"OCR failed: {exc}", is_error=True)

    async def _do_click_text(self, inp: dict[str, Any]) -> ToolResult:
        """Find text on screen via OCR and click on it."""
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            return ToolResult(
                output="click_text requires pytesseract and Pillow. Install with: pip install pytesseract Pillow",
                is_error=True,
            )

        target = str(inp.get("target_text", "")).strip()
        if not target:
            return ToolResult(output="click_text requires 'target_text'", is_error=True)

        language = str(inp.get("language", "chi_sim+eng"))
        button = str(inp.get("button", "left"))

        try:
            sct = self._ensure_mss()
            monitor = sct.monitors[1]
            raw = sct.grab(monitor)
            import mss.tools
            png_bytes = mss.tools.to_png(raw.rgb, raw.size)
            img = Image.open(io.BytesIO(png_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")

            data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)
            best_match = None
            best_score = -1
            best_conf = -1
            target_lower = target.lower()
            n_boxes = len(data["text"])
            for i in range(n_boxes):
                text = str(data["text"][i]).strip()
                if not text:
                    continue
                text_lower = text.lower()
                # Prefer exact match, then substring, then partial word
                if text_lower == target_lower:
                    score = 3
                elif target_lower in text_lower:
                    score = 2
                elif any(part in text_lower for part in target_lower.split()):
                    score = 1
                else:
                    continue
                conf = int(data["conf"][i]) if str(data["conf"][i]).lstrip("-").isdigit() else -1
                # Tie-break by confidence
                if score > best_score or (score == best_score and conf > best_conf):
                    best_score = score
                    best_conf = conf
                    x = int(data["left"][i])
                    y = int(data["top"][i])
                    w = int(data["width"][i])
                    h = int(data["height"][i])
                    best_match = (x + w // 2, y + h // 2, text, conf, w, h)

            if best_match is None:
                screenshot_result = await self._do_screenshot({})
                return ToolResult(
                    output=f"click_text: could not find '{target}' on screen",
                    is_error=True,
                    base64_image=screenshot_result.base64_image,
                )

            cx, cy, matched_text, conf, tw, th = best_match
            tx, ty = self._jittered_click_coords(cx, cy, tw, th)
            await self._do_mouse_move({"x": tx, "y": ty})
            time.sleep(0.05)
            down_flag, up_flag = {
                "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
                "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
                "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
            }.get(button, (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
            self._send_mouse_event(down_flag)
            time.sleep(0.05)
            self._send_mouse_event(up_flag)
            time.sleep(0.1)
            screenshot_result = await self._do_screenshot({})
            return ToolResult(
                output=f"Clicked on text '{matched_text}' at ({tx}, {ty}) with {button} button (ocr_conf={conf})",
                base64_image=screenshot_result.base64_image,
                details={"matched_text": matched_text, "x": tx, "y": ty, "ocr_confidence": conf},
            )
        except Exception as exc:
            return ToolResult(output=f"click_text failed: {exc}", is_error=True)

    async def _do_window_manage(self, inp: dict[str, Any]) -> ToolResult:
        """Manage windows: move, resize, minimize, maximize, restore, close."""
        title = str(inp.get("title", "")).strip().lower()
        window_action = str(inp.get("window_action", "")).strip().lower()
        if not title:
            return ToolResult(output="window_manage requires 'title'", is_error=True)
        if not window_action:
            return ToolResult(output="window_manage requires 'window_action'", is_error=True)
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
            if window_action == "move":
                x = inp.get("x", 0)
                y = inp.get("y", 0)
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, x, y, 0, 0, win32con.SWP_NOSIZE)
                return ToolResult(output=f"Moved window '{full_title}' to ({x}, {y})")
            elif window_action == "resize":
                width = inp.get("width", 800)
                height = inp.get("height", 600)
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, 0, 0, width, height, win32con.SWP_NOMOVE)
                return ToolResult(output=f"Resized window '{full_title}' to {width}x{height}")
            elif window_action == "minimize":
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                return ToolResult(output=f"Minimized window '{full_title}'")
            elif window_action == "maximize":
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                return ToolResult(output=f"Maximized window '{full_title}'")
            elif window_action == "restore":
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                return ToolResult(output=f"Restored window '{full_title}'")
            elif window_action == "close":
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                return ToolResult(output=f"Closed window '{full_title}'")
            else:
                return ToolResult(output=f"Unknown window_action: {window_action}", is_error=True)
        except Exception as exc:
            return ToolResult(output=f"window_manage failed for '{full_title}': {exc}", is_error=True)

    async def _do_shell(self, inp: dict[str, Any]) -> ToolResult:
        """Execute a shell command and return stdout/stderr."""
        command = str(inp.get("command", "")).strip()
        if not command:
            return ToolResult(output="shell requires 'command'", is_error=True)
        timeout = max(1, min(300, int(inp.get("timeout", 30))))
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=True,
            )
            stdout = result.stdout[:4000] if result.stdout else ""
            stderr = result.stderr[:2000] if result.stderr else ""
            output_lines = []
            if stdout:
                output_lines.append(f"STDOUT:\n{stdout}")
            if stderr:
                output_lines.append(f"STDERR:\n{stderr}")
            if not output_lines:
                output_lines.append("(no output)")
            output_lines.append(f"Exit code: {result.returncode}")
            return ToolResult(
                output="\n".join(output_lines),
                details={"returncode": result.returncode, "command": command},
            )
        except subprocess.TimeoutExpired:
            return ToolResult(output=f"shell timed out after {timeout}s", is_error=True)
        except Exception as exc:
            return ToolResult(output=f"shell error: {exc}", is_error=True)

    async def _do_batch(self, inp: dict[str, Any]) -> ToolResult:
        """Execute multiple computer actions sequentially in one tool call."""
        steps = inp.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return ToolResult(output="batch requires 'steps' array", is_error=True)
        bail = inp.get("bail", True)
        outputs = []
        final_result = None
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                outputs.append(f"Step {i + 1}: invalid step (not an object)")
                if bail:
                    break
                continue
            action = step.get("action", "")
            if not action:
                outputs.append(f"Step {i + 1}: missing action")
                if bail:
                    break
                continue
            try:
                result = await self._dispatch(action, step)
                final_result = result
                prefix = f"Step {i + 1} ({action}):"
                if result.is_error:
                    outputs.append(f"{prefix} ERROR - {result.output}")
                    if bail:
                        break
                else:
                    outputs.append(f"{prefix} OK - {result.output}")
            except Exception as exc:
                outputs.append(f"Step {i + 1} ({action}): EXCEPTION - {exc}")
                if bail:
                    break
        summary = "\n".join(outputs)
        # Return final screenshot if available, or capture a new one
        image = final_result.base64_image if final_result and final_result.base64_image else None
        if image is None:
            try:
                screenshot_result = await self._do_screenshot({})
                image = screenshot_result.base64_image
            except Exception:
                pass
        has_error = any("ERROR" in line or "EXCEPTION" in line or "invalid" in line or "missing" in line for line in outputs)
        # bail=false means "don't stop on error", NOT "treat errors as success" —
        # callers (and audit-stats) still need to know if any sub-step failed,
        # otherwise the only place the failure surfaces is in the summary string.
        return ToolResult(
            output=summary,
            is_error=has_error,
            base64_image=image,
        )

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
