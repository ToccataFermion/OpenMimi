"""AgentBrowserTool: wraps the agent-browser Rust CLI for Anthropic-style tool actions.

Design intent: agent-browser is a sidecar process (Rust CLI) that speaks CDP.
We communicate via subprocess, parse --json output, and translate into OpenMimi's
ToolResult format.

Key workflow difference from BrowserTool:
- LLM must first call snapshot to discover @eN refs
- Subsequent actions use refs for precise, stable targeting
- Text-based locators (find text "..." click) are available as fallback
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .base import ToolBase
from .errors import ErrorCode
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Operate a Chromium browser via agent-browser (Rust CLI). "
    "Core workflow: 1) Call action='snapshot' to get an accessibility tree with @eN refs; "
    "2) Use those refs in action='click' / 'type' / 'fill' / 'hover' / 'drag' via the 'ref' field; "
    "3) Call action='screenshot' when visual verification is needed. "
    "If no ref is known, use 'target_text' for semantic text matching. "
    "Navigation: action='navigate' with 'url', or 'back' / 'forward' / 'reload'. "
    "Tabs: action='tab_list' or action='tab_switch' with 'tab_index' (1-based). "
    "Checkbox: use action='check' or 'uncheck' with 'ref' (never click checkboxes). "
    "Dropdown: use action='select' with 'ref' and 'value'. "
    "File upload: use action='upload' with 'ref' and 'file_path'. "
    "File download: use action='download' with 'ref' and 'save_path'. "
    "Drag and drop: action='drag' with 'ref'+'to_ref' or 'target_text'+'to_target_text'. "
    "Low-level mouse control: action='mouse' with 'mouse_action' (move/down/up/wheel). "
    "Use mouse sequences (move -> down -> move -> up) for interactions that standard click "
    "cannot handle, such as dragging sliders, drawing, or any press-and-hold gesture. "
    "Window focus: action='focus' brings the browser window to the foreground (useful "
    "before OS-level mouse actions like computer.mouse_drag on CAPTCHAs). "
    "Element coordinates: action='get_box' with 'ref' or 'target_text' returns the "
    "element's bounding box (x, y, width, height) for OS-level mouse coordination. "
    "Dynamic content: action='wait_for' with 'ref', 'target_text', or 'text' waits until "
    "the element or text appears on the page (useful for React/Vue SPAs that render lazily). "
    "For multi-step atomic execution, use action='batch' with 'steps'."
)

_DEFAULT_TIMEOUT_S = 300.0
_DAEMON_WARMUP_TIMEOUT_S = 600.0
_SCREENSHOT_DIR = Path(tempfile.gettempdir()) / "agent_browser_screenshots"

# CAPTCHA indicators in page text (Chinese + English)
_CAPTCHA_KEYWORDS = [
    "验证码", "滑块", "拼图", "拖动", "人机验证", "请向右滑动",
    "captcha", "recaptcha", "geetest", "hcaptcha",
    "verify you are human", "slide to verify", "drag to verify",
    "i'm not a robot", "我不是机器人",
    "请完成安全验证", "安全验证", "验证通过",
    "点击验证", "智能验证", "行为验证",
]

class AgentBrowserTool(ToolBase):
    name = "agent_browser"

    def __init__(
        self,
        *,
        download_dir: str,
        viewport: tuple[int, int] = (1280, 800),
        headless: bool = False,
        executable: str = "agent-browser",
        browser_args: list[str] | None = None,
    ) -> None:
        self._download_dir = Path(download_dir)
        self._viewport = viewport
        self._headless = headless
        self._browser_args = browser_args or []
        # Resolve executable path (npm .cmd wrappers on Windows need shell or full path)
        resolved = shutil.which(executable)
        if resolved:
            self._executable = resolved
        else:
            self._executable = executable
        self._started = False
        self._tabs: list[dict[str, Any]] = []
        self._active_tab_index = 1
        self._session_name = f"openmimi_{os.getpid()}_{int(time.time())}"
        _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        # npm .cmd wrappers garble JavaScript quotes/metacharacters when
        # shell=True (cmd.exe strips quotes, interprets parentheses, etc.).
        # Bypass the .cmd wrapper and run the native .exe directly.
        if sys.platform == "win32" and self._executable.lower().endswith((".cmd", ".bat")):
            exe_path = Path(self._executable).parent / "node_modules" / "agent-browser" / "bin" / "agent-browser-win32-x64.exe"
            if exe_path.exists():
                self._executable = str(exe_path)
                self._use_shell = False
            else:
                self._use_shell = True
        else:
            self._use_shell = False
        self._warmup_thread: threading.Thread | None = None
        self._start_warmup()

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

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
                            "navigate",
                            "back",
                            "forward",
                            "reload",
                            "snapshot",
                            "click",
                            "check",
                            "uncheck",
                            "type",
                            "fill",
                            "press",
                            "hover",
                            "scroll",
                            "screenshot",
                            "extract",
                            "select",
                            "upload",
                            "download",
                            "tab_list",
                            "tab_switch",
                            "tab_new",
                            "tab_close",
                            "wait",
                            "eval",
                            "batch",
                            "close",
                            "drag",
                            "mouse",
                            "focus",
                            "clipboard",
                            "get_box",
                            "wait_for",
                        ],
                        "description": "The browser action to perform.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Element reference from snapshot, e.g. @e3. Preferred over target_text.",
                    },
                    "target_text": {
                        "type": "string",
                        "description": "Visible text to locate the element when ref is unavailable.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Use low-level mouse down/up sequence instead of standard click. Useful for React SPAs and elements that ignore synthetic click events.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Text to type or fill.",
                    },
                    "url": {
                        "type": "string",
                        "description": "URL for navigate or tab_new.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                        "description": "Scroll direction.",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Scroll amount in pixels (default 500).",
                    },
                    "key": {
                        "type": "string",
                        "description": "Key to press, e.g. Enter, Escape, Tab.",
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "1-based tab index for tab_switch or tab_close.",
                    },
                    "milliseconds": {
                        "type": "integer",
                        "description": "Wait time in milliseconds.",
                    },
                    "js": {
                        "type": "string",
                        "description": "JavaScript code to evaluate.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of agent-browser commands for batch action.",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "For extract: what to extract from the page (e.g. 'get text').",
                    },
                    "to_ref": {
                        "type": "string",
                        "description": "Target element reference for drag action, e.g. @e5.",
                    },
                    "to_target_text": {
                        "type": "string",
                        "description": "Target element visible text for drag action.",
                    },
                    "mouse_action": {
                        "type": "string",
                        "enum": ["move", "down", "up", "wheel"],
                        "description": "Mouse subcommand for action='mouse'.",
                    },
                    "x": {
                        "type": "integer",
                        "description": "X coordinate for mouse move.",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate for mouse move.",
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button for down/up (default left).",
                    },
                    "clipboard_action": {
                        "type": "string",
                        "enum": ["read", "write", "copy", "paste"],
                        "description": "Clipboard subcommand for action='clipboard'.",
                    },
                    "clipboard_text": {
                        "type": "string",
                        "description": "Text to write to clipboard (for clipboard_action='write').",
                    },
                    "annotate": {
                        "type": "boolean",
                        "description": "Add numbered labels to screenshot for vision model reference (action='screenshot' only).",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Option values for action='select' (dropdown).",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Local file path for action='upload' or target path for action='download'.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to wait for on the page (action='wait_for').",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Timeout in milliseconds for wait_for (default 10000).",
                    },
                    "interval_ms": {
                        "type": "integer",
                        "description": "Polling interval in milliseconds for wait_for (default 500).",
                    },
                },
                "required": ["action"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        action = tool_input.get("action", "")

        if action == "close":
            return await self._do_close()

        if not self._started and action != "navigate":
            # Auto-start on first non-close action
            await self._start_browser()

        try:
            return await self._dispatch(action, tool_input)
        except Exception as e:
            return ToolResult(output=f"Error: {e}", is_error=True)

    async def close(self) -> None:
        if self._started:
            try:
                await self._exec("close", "--all")
            except Exception:
                pass
            self._started = False

    # ------------------------------------------------------------------ #
    #  Dispatch
    # ------------------------------------------------------------------ #

    async def _dispatch(self, action: str, inp: dict[str, Any]) -> ToolResult:
        handlers: dict[str, Any] = {
            "navigate": self._do_navigate,
            "back": self._do_back,
            "forward": self._do_forward,
            "reload": self._do_reload,
            "snapshot": self._do_snapshot,
            "click": self._do_click,
            "check": self._do_check,
            "uncheck": self._do_uncheck,
            "type": self._do_type,
            "fill": self._do_fill,
            "press": self._do_press,
            "hover": self._do_hover,
            "scroll": self._do_scroll,
            "screenshot": self._do_screenshot,
            "extract": self._do_extract,
            "select": self._do_select,
            "upload": self._do_upload,
            "download": self._do_download,
            "tab_list": self._do_tab_list,
            "tab_switch": self._do_tab_switch,
            "tab_new": self._do_tab_new,
            "tab_close": self._do_tab_close,
            "wait": self._do_wait,
            "eval": self._do_eval,
            "batch": self._do_batch,
            "drag": self._do_drag,
            "mouse": self._do_mouse,
            "focus": self._do_focus,
            "clipboard": self._do_clipboard,
            "get_box": self._do_get_box,
            "wait_for": self._do_wait_for,
        }
        handler = handlers.get(action)
        if not handler:
            return ToolResult(output=f"Unknown action: {action}")
        return await handler(inp)

    # ------------------------------------------------------------------ #
    #  Action implementations
    # ------------------------------------------------------------------ #

    async def _do_navigate(self, inp: dict[str, Any]) -> ToolResult:
        url = inp.get("url", "about:blank")
        if not self._started:
            await self._start_browser(url)
        else:
            await self._exec("open", url, "--json")
        # Refresh tab state after navigation
        await self._refresh_tabs()
        snapshot = await self._exec("snapshot", "--json")
        text, _ = self._parse_snapshot(snapshot.stdout)

        # Retry once if page is still empty (slow initial load or first startup)
        if "(empty page)" in text:
            await asyncio.sleep(3)
            await self._refresh_tabs()
            snapshot = await self._exec("snapshot", "--json")
            text, _ = self._parse_snapshot(snapshot.stdout)

        image = await self._take_screenshot()
        details = {
            "url": url,
            "open_tabs": self._tabs,
            "active_tab": self._active_tab_index,
        }
        return ToolResult(
            output=f"Navigated to {url}\n{text[:2000]}",
            base64_image=image,
            details=details,
        )

    async def _do_back(self, _inp: dict[str, Any]) -> ToolResult:
        result = await self._exec("back", "--json")
        image = await self._take_screenshot()
        return ToolResult(output=f"Navigated back\n{result.stdout[:1000]}", base64_image=image)

    async def _do_forward(self, _inp: dict[str, Any]) -> ToolResult:
        result = await self._exec("forward", "--json")
        image = await self._take_screenshot()
        return ToolResult(output=f"Navigated forward\n{result.stdout[:1000]}", base64_image=image)

    async def _do_reload(self, _inp: dict[str, Any]) -> ToolResult:
        result = await self._exec("reload", "--json")
        image = await self._take_screenshot()
        return ToolResult(output=f"Page reloaded\n{result.stdout[:1000]}", base64_image=image)

    async def _do_select(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        options = inp.get("options", [])
        if not options:
            return ToolResult(output="select requires 'options' array", is_error=True)
        selector = ref or target_text
        if not selector:
            return ToolResult(output="select requires 'ref' or 'target_text'", is_error=True)
        args = ["select", selector] + [str(o) for o in options] + ["--json"]
        result = await self._exec(*args)
        image = await self._take_screenshot()
        return ToolResult(output=f"Selected {options} on {selector}\n{result.stdout[:1000]}", base64_image=image)

    async def _do_upload(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        file_path = inp.get("file_path")
        if not file_path:
            return ToolResult(output="upload requires 'file_path'", is_error=True)
        selector = ref or target_text
        if not selector:
            return ToolResult(output="upload requires 'ref' or 'target_text'", is_error=True)
        result = await self._exec("upload", selector, file_path, "--json")
        image = await self._take_screenshot()
        return ToolResult(output=f"Uploaded {file_path} to {selector}\n{result.stdout[:1000]}", base64_image=image)

    async def _do_download(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        save_path = inp.get("file_path") or inp.get("save_path")
        if not save_path:
            return ToolResult(output="download requires 'file_path' (save location)", is_error=True)
        selector = ref or target_text
        if not selector:
            return ToolResult(output="download requires 'ref' or 'target_text'", is_error=True)
        result = await self._exec("download", selector, save_path, "--json")
        image = await self._take_screenshot()
        return ToolResult(output=f"Downloaded to {save_path} from {selector}\n{result.stdout[:1000]}", base64_image=image)

    async def _do_snapshot(self, inp: dict[str, Any]) -> ToolResult:
        snapshot = await self._exec("snapshot", "--json")
        text, refs = self._parse_snapshot(snapshot.stdout)
        image = await self._take_screenshot()
        details = {
            "open_tabs": self._tabs,
            "active_tab": self._active_tab_index,
            "refs": refs,
        }
        # Detect CAPTCHA
        captcha_info = await self._detect_captcha(text)
        if captcha_info:
            details["captcha_detected"] = True
            details["captcha_type"] = captcha_info["type"]
            # Return as a normal result (not an error) so the LLM can continue
            # reasoning and use ComputerTool to solve the CAPTCHA visually.
            return ToolResult(
                output=(
                    f"A CAPTCHA challenge is present on the page. "
                    f"Type: {captcha_info['type']}. "
                    f"You may analyze the screenshot to solve it.\n\n"
                    f"Snapshot:\n{text}"
                ),
                base64_image=image,
                is_error=False,
                details={
                    **details,
                    "error_code": ErrorCode.CAPTCHA_DETECTED,
                },
            )
        return ToolResult(
            output=f"Snapshot:\n{text}",
            base64_image=image,
            details=details,
        )

    async def _do_click(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        force = inp.get("force", False)
        selector = ref or target_text
        if not selector:
            return ToolResult(output="click requires 'ref' or 'target_text'")

        if force:
            return await self._click_with_mouse(selector)

        # Standard click first
        if ref:
            result = await self._exec("click", ref, "--json")
        else:
            result = await self._exec(
                "find", "text", target_text, "click", "--json"
            )
        data = self._parse_data(result.stdout)
        # Check if a new tab was opened and auto-switch
        await self._switch_to_newest_tab()
        image = await self._take_screenshot()
        clicked = data.get("clicked", "element")
        details = {
            "open_tabs": self._tabs,
            "active_tab": self._active_tab_index,
        }
        return ToolResult(
            output=f"Clicked {clicked}",
            base64_image=image,
            details=details,
        )

    async def _click_with_mouse(self, selector: str) -> ToolResult:
        """Fallback click using mouse move/down/up via CDP.

        This bypasses synthetic click event limitations on React SPAs
        and other pages that ignore standard automation clicks.
        """
        try:
            result = await self._exec("get", "box", selector, "--json")
            data = self._parse_data(result.stdout)
            box = data.get("box") if isinstance(data, dict) else None
            if not box:
                return ToolResult(
                    output=f"force click failed: could not get box for {selector}",
                    is_error=True,
                )
            x = int(box.get("x", 0) + box.get("width", 0) / 2)
            y = int(box.get("y", 0) + box.get("height", 0) / 2)
        except Exception as exc:
            return ToolResult(
                output=f"force click failed getting box: {exc}",
                is_error=True,
            )

        # Mouse sequence: move -> down -> up
        await self._exec("mouse", "move", str(x), str(y), "--json")
        await asyncio.sleep(0.05)
        await self._exec("mouse", "down", "left", "--json")
        await asyncio.sleep(0.05)
        await self._exec("mouse", "up", "left", "--json")
        await asyncio.sleep(0.1)

        image = await self._take_screenshot()
        return ToolResult(
            output=f"Force-clicked {selector} at ({x}, {y}) via mouse down/up",
            base64_image=image,
        )

    async def _do_check(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        if ref:
            result = await self._exec("check", ref, "--json")
        elif target_text:
            result = await self._exec(
                "find", "text", target_text, "check", "--json"
            )
        else:
            return ToolResult(output="check requires 'ref' or 'target_text'")
        image = await self._take_screenshot()
        return ToolResult(output="Checked element", base64_image=image)

    async def _do_uncheck(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        if ref:
            result = await self._exec("uncheck", ref, "--json")
        elif target_text:
            result = await self._exec(
                "find", "text", target_text, "uncheck", "--json"
            )
        else:
            return ToolResult(output="uncheck requires 'ref' or 'target_text'")
        image = await self._take_screenshot()
        return ToolResult(output="Unchecked element", base64_image=image)

    async def _do_type(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        value = inp.get("value", "")
        if ref:
            result = await self._exec("type", ref, value, "--json")
        elif target_text:
            result = await self._exec(
                "find", "text", target_text, "type", value, "--json"
            )
        else:
            return ToolResult(output="type requires 'ref' or 'target_text'")
        image = await self._take_screenshot()
        return ToolResult(
            output=f"Typed {len(value)} character(s)",
            base64_image=image,
        )

    async def _do_fill(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        value = inp.get("value", "")
        if ref:
            result = await self._exec("fill", ref, value, "--json")
        elif target_text:
            result = await self._exec(
                "find", "text", target_text, "fill", value, "--json"
            )
        else:
            return ToolResult(output="fill requires 'ref' or 'target_text'")
        image = await self._take_screenshot()
        return ToolResult(
            output=f"Filled with {len(value)} character(s)",
            base64_image=image,
        )

    async def _do_press(self, inp: dict[str, Any]) -> ToolResult:
        key = inp.get("key", "Enter")
        result = await self._exec("press", key, "--json")
        image = await self._take_screenshot()
        return ToolResult(output=f"Pressed {key}", base64_image=image)

    async def _do_hover(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        if ref:
            result = await self._exec("hover", ref, "--json")
        elif target_text:
            result = await self._exec(
                "find", "text", target_text, "hover", "--json"
            )
        else:
            return ToolResult(output="hover requires 'ref' or 'target_text'")
        image = await self._take_screenshot()
        return ToolResult(output="Hovered element", base64_image=image)

    async def _do_scroll(self, inp: dict[str, Any]) -> ToolResult:
        direction = inp.get("direction", "down")
        amount = inp.get("amount", 500)
        result = await self._exec("scroll", direction, str(amount), "--json")
        image = await self._take_screenshot()
        return ToolResult(
            output=f"Scrolled {direction} {amount}px",
            base64_image=image,
        )

    async def _do_screenshot(self, inp: dict[str, Any]) -> ToolResult:
        path = inp.get("path")
        annotate = inp.get("annotate", False)
        image = await self._take_screenshot(path_override=path, annotate=annotate)
        label = "Annotated screenshot" if annotate else "Screenshot"
        return ToolResult(output=f"{label} taken", base64_image=image)

    async def _do_clipboard(self, inp: dict[str, Any]) -> ToolResult:
        cb_action = inp.get("clipboard_action", "read")
        if cb_action == "read":
            result = await self._exec("clipboard", "read", "--json")
            data = self._parse_data(result.stdout)
            text = data.get("text", "")
            return ToolResult(output=f"Clipboard: {text}")
        elif cb_action == "write":
            text = str(inp.get("clipboard_text", ""))
            await self._exec("clipboard", "write", text, "--json")
            return ToolResult(output=f"Wrote {len(text)} chars to clipboard")
        elif cb_action == "copy":
            await self._exec("clipboard", "copy", "--json")
            return ToolResult(output="Copied current selection to clipboard")
        elif cb_action == "paste":
            await self._exec("clipboard", "paste", "--json")
            return ToolResult(output="Pasted clipboard content")
        else:
            return ToolResult(output=f"Unknown clipboard action: {cb_action}", is_error=True)

    async def _do_extract(self, inp: dict[str, Any]) -> ToolResult:
        instruction = inp.get("instruction", "get text")
        if instruction == "get text":
            result = await self._exec(
                "eval", "document.body.innerText", "--json"
            )
            data = self._parse_data(result.stdout)
            text = data.get("result", "")
            return ToolResult(output=text[:4000])
        else:
            # Generic extraction via eval
            js = f"({{url: window.location.href, title: document.title, text: document.body.innerText}})"
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            return ToolResult(output=json.dumps(data.get("result"), ensure_ascii=False, indent=2)[:4000])

    async def _do_tab_list(self, inp: dict[str, Any]) -> ToolResult:
        await self._refresh_tabs()
        lines = [f"Tab {i+1}: {t.get('url', '')}" for i, t in enumerate(self._tabs)]
        active = self._tabs[self._active_tab_index - 1] if self._tabs else {}
        return ToolResult(
            output=f"Active tab: {self._active_tab_index}\n" + "\n".join(lines),
            details={"open_tabs": self._tabs, "active_tab": self._active_tab_index},
        )

    async def _do_tab_switch(self, inp: dict[str, Any]) -> ToolResult:
        idx = inp.get("tab_index", 1)
        await self._refresh_tabs()
        if 1 <= idx <= len(self._tabs):
            tab_id = self._tabs[idx - 1].get("id", f"t{idx}")
            await self._exec("tab", tab_id, "--json")
            self._active_tab_index = idx
            image = await self._take_screenshot()
            return ToolResult(
                output=f"Switched to tab {idx}",
                base64_image=image,
                details={"open_tabs": self._tabs, "active_tab": idx},
            )
        return ToolResult(output=f"Invalid tab index {idx}")

    async def _do_tab_new(self, inp: dict[str, Any]) -> ToolResult:
        url = inp.get("url", "about:blank")
        result = await self._exec("tab", "new", url, "--json")
        await self._refresh_tabs()
        image = await self._take_screenshot()
        return ToolResult(
            output=f"New tab opened: {url}",
            base64_image=image,
            details={"open_tabs": self._tabs, "active_tab": self._active_tab_index},
        )

    async def _do_tab_close(self, inp: dict[str, Any]) -> ToolResult:
        idx = inp.get("tab_index")
        await self._refresh_tabs()
        if idx and 1 <= idx <= len(self._tabs):
            tab_id = self._tabs[idx - 1].get("id", f"t{idx}")
            await self._exec("tab", "close", tab_id, "--json")
            await self._refresh_tabs()
            return ToolResult(output=f"Closed tab {idx}")
        return ToolResult(output="tab_close requires valid tab_index")

    async def _do_wait(self, inp: dict[str, Any]) -> ToolResult:
        ms = inp.get("milliseconds", 1000)
        await self._exec("wait", str(ms), "--json")
        return ToolResult(output=f"Waited {ms}ms")

    async def _do_eval(self, inp: dict[str, Any]) -> ToolResult:
        js = inp.get("js", "")
        if not js.strip():
            return ToolResult(
                output="eval requires non-empty 'js' field", is_error=True
            )
        result = await self._exec("eval", js, "--json")
        raw = self._parse_json(result.stdout)
        if not isinstance(raw, dict):
            return ToolResult(
                output=f"Invalid eval response: {result.stdout[:200]}",
                is_error=True,
            )
        if raw.get("success") is False:
            err = raw.get("error") or "eval failed"
            return ToolResult(output=f"Eval error: {err}", is_error=True)
        data = raw.get("data", {})
        result_value = data.get("result") if isinstance(data, dict) else None
        return ToolResult(
            output=json.dumps(result_value, ensure_ascii=False, indent=2)
        )

    async def _do_batch(self, inp: dict[str, Any]) -> ToolResult:
        steps = inp.get("steps", [])
        if not steps:
            return ToolResult(output="batch requires 'steps' array")
        # Build batch command
        args = ["batch", "--bail", "--json"] + steps
        result = await self._exec(*args)
        data = self._parse_data(result.stdout)
        image = await self._take_screenshot()
        return ToolResult(
            output=json.dumps(data, ensure_ascii=False, indent=2)[:4000],
            base64_image=image,
        )

    async def _do_drag(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        to_ref = inp.get("to_ref")
        to_target_text = inp.get("to_target_text")
        if ref and to_ref:
            result = await self._exec("drag", ref, to_ref, "--json")
        elif target_text and to_target_text:
            # agent-browser find syntax: find <locator> <value> <action> ...
            # Drag target is an element ref/selector, not text, so this path
            # is best-effort. Prefer refs for drag.
            result = await self._exec(
                "find", "text", target_text, "drag", to_target_text, "--json"
            )
        else:
            return ToolResult(
                output="drag requires 'ref'+'to_ref' or 'target_text'+'to_target_text'"
            )
        image = await self._take_screenshot()
        return ToolResult(output="Dragged element", base64_image=image)

    async def _do_mouse(self, inp: dict[str, Any]) -> ToolResult:
        mouse_action = inp.get("mouse_action", "move")
        if mouse_action == "move":
            x = inp.get("x", 0)
            y = inp.get("y", 0)
            result = await self._exec("mouse", "move", str(x), str(y), "--json")
        elif mouse_action == "down":
            btn = inp.get("button", "left")
            result = await self._exec("mouse", "down", btn, "--json")
        elif mouse_action == "up":
            btn = inp.get("button", "left")
            result = await self._exec("mouse", "up", btn, "--json")
        elif mouse_action == "wheel":
            dy = inp.get("amount", 0)
            dx = inp.get("x", 0)
            result = await self._exec("mouse", "wheel", str(dy), str(dx), "--json")
        else:
            return ToolResult(output=f"Unknown mouse_action: {mouse_action}")
        image = await self._take_screenshot()
        return ToolResult(output=f"Mouse {mouse_action}", base64_image=image)

    async def _do_focus(self, _inp: dict[str, Any]) -> ToolResult:
        """Bring the browser window to the foreground using win32gui."""
        try:
            import win32gui
            import win32con
        except ImportError:
            return ToolResult(output="win32gui not available", is_error=True)

        # Get current page title/URL to identify the right window
        try:
            result = await self._exec(
                "eval",
                "(() => ({title: document.title, href: window.location.href}))()",
                "--json",
            )
            raw = self._parse_json(result.stdout)
            page_info: dict[str, str] = {}
            if isinstance(raw, dict) and raw.get("success") is True:
                data = raw.get("data", {})
                res = data.get("result") if isinstance(data, dict) else {}
                if isinstance(res, dict):
                    page_info = res
        except Exception:
            page_info = {}

        title_hint = page_info.get("title", "").strip().lower()
        url_hint = page_info.get("href", "").strip().lower()
        domain = ""
        if url_hint:
            from urllib.parse import urlparse
            try:
                domain = urlparse(url_hint).netloc.lower()
            except Exception:
                pass

        def _enum(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                wt = win32gui.GetWindowText(hwnd).lower()
                extra.append((hwnd, wt))
            return True

        candidates = []
        win32gui.EnumWindows(_enum, candidates)

        # Score each candidate: higher = better match
        best: tuple[int, str, int] | None = None
        for hwnd, wt in candidates:
            score = 0
            if title_hint and title_hint in wt:
                score += 3
            if domain and domain in wt:
                score += 2
            if url_hint and url_hint in wt:
                score += 2
            # Generic Chromium/Electron indicators
            if "chromium" in wt or "chrome" in wt:
                score += 1
            if score > 0 and (best is None or score > best[2]):
                best = (hwnd, wt, score)

        if best is None:
            return ToolResult(
                output="Could not find a visible browser window to focus",
                is_error=True,
            )

        hwnd, wt, _score = best
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as exc:
            return ToolResult(
                output=f"Failed to focus browser window '{wt}': {exc}",
                is_error=True,
            )
        return ToolResult(output=f"Browser window focused: {wt}")

    async def _do_get_box(self, inp: dict[str, Any]) -> ToolResult:
        """Return the bounding box of an element for OS-level mouse coordination."""
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        selector = ref or target_text
        if not selector:
            return ToolResult(output="get_box requires 'ref' or 'target_text'", is_error=True)
        try:
            result = await self._exec("get", "box", selector, "--json")
            data = self._parse_data(result.stdout)
            box = data.get("box") if isinstance(data, dict) else None
            if not box:
                return ToolResult(
                    output=f"Could not get box for {selector}", is_error=True
                )
            return ToolResult(
                output=json.dumps(box, ensure_ascii=False, indent=2),
                details={"box": box, "selector": selector},
            )
        except Exception as exc:
            return ToolResult(
                output=f"get_box failed for {selector}: {exc}", is_error=True
            )

    async def _do_wait_for(self, inp: dict[str, Any]) -> ToolResult:
        """Wait for an element or text to appear on the page."""
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        text = inp.get("text", "")
        timeout_ms = inp.get("timeout_ms", 10000)
        interval_ms = inp.get("interval_ms", 500)
        selector = ref or target_text

        if not selector and not text:
            return ToolResult(
                output="wait_for requires 'ref', 'target_text', or 'text'", is_error=True
            )

        start = time.monotonic()
        while (time.monotonic() - start) * 1000 < timeout_ms:
            try:
                if selector:
                    result = await self._exec("get", "box", selector, "--json")
                    data = self._parse_data(result.stdout)
                    box = data.get("box") if isinstance(data, dict) else None
                    if box:
                        return ToolResult(
                            output=f"Element found: {selector}",
                            details={"box": box, "selector": selector},
                        )
                if text:
                    snapshot = await self._exec("snapshot", "--json")
                    snap_text, _ = self._parse_snapshot(snapshot.stdout)
                    if text in snap_text:
                        return ToolResult(
                            output=f"Text found: {text}",
                        )
            except Exception:
                pass
            await asyncio.sleep(interval_ms / 1000.0)

        return ToolResult(
            output=f"wait_for timed out after {timeout_ms}ms: {selector or text}",
            is_error=True,
        )

    async def _do_close(self) -> ToolResult:
        if self._started:
            await self._exec("close", "--all", "--json")
            self._started = False
        return ToolResult(output="Browser closed.")

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    async def _detect_captcha(self, snapshot_text: str) -> dict[str, str] | None:
        """Check for active CAPTCHA/verification challenge indicators.

        Requires BOTH a visible CAPTCHA element AND matching keywords in the
        snapshot text.  This two-signal approach eliminates false positives
        from login pages that contain CAPTCHA instructional text or unrelated
        elements with captcha-like class names.

        Returns a dict with 'type' and 'message' if detected, else None.
        """
        if not snapshot_text:
            return None

        text_lower = snapshot_text.lower()

        # 1) Keyword signal – must be present for any detection
        keyword_match = None
        for keyword in _CAPTCHA_KEYWORDS:
            if keyword.lower() in text_lower:
                keyword_match = keyword
                break
        if not keyword_match:
            return None

        # 2) Element signal – a known CAPTCHA container must be visible
        js = """
        (() => {
            const selectors = [
                '.xftImageVerify', '.imageVerifyDragButton', '.bottomImage',
                '.dragImage', '.imageVerify',
                '.geetest_holder', '.geetest_box', '.geetest_widget',
                '#captcha', '.captcha',
                '.nc-container', '.nc_wrapper',
                '.hcaptcha', '.h-captcha',
                '.g-recaptcha', '.recaptcha',
                '.yidun', '.yidun_panel',
                '.slideCode', '.verify-code',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    if (style.display !== 'none' && style.visibility !== 'hidden' &&
                        rect.width > 10 && rect.height > 10) {
                        return {found: true, selector: sel};
                    }
                }
            }
            return {found: false};
        })()
        """
        element_found = False
        try:
            result = await self._exec("eval", js, "--json")
            raw = self._parse_json(result.stdout)
            if isinstance(raw, dict) and raw.get("success") is True:
                data = raw.get("data", {})
                result_value = data.get("result") if isinstance(data, dict) else {}
                element_found = isinstance(result_value, dict) and result_value.get("found")
        except Exception:
            pass

        if not element_found:
            # No visible CAPTCHA element – do not report even if keywords are present.
            # This prevents false positives from instructional UI text.
            return None

        # Both signals present – classify and report
        if ("滑块" in snapshot_text or "拖动" in snapshot_text or
            "slide" in text_lower):
            return {"type": "slider", "message": "Slider CAPTCHA detected. Analyze the screenshot to solve it."}
        if ("点击" in snapshot_text or "click" in text_lower):
            return {"type": "click", "message": "Click CAPTCHA detected. Analyze the screenshot to solve it."}
        return {"type": "unknown", "message": "CAPTCHA/verification challenge detected. Analyze the screenshot to solve it."}

    def _browser_env(self) -> dict[str, str] | None:
        """Return environment dict with AGENT_BROWSER_ARGS set if needed.

        Using the env var instead of --args CLI flag avoids a bug where
        --args combined with --headed causes navigation to fail on some
        sites (the page opens but the active tab stays about:blank).
        """
        if not self._browser_args:
            return None
        env = os.environ.copy()
        env["AGENT_BROWSER_ARGS"] = ",".join(self._browser_args)
        return env

    def _start_warmup(self) -> None:
        """Fire a background thread that starts the agent-browser daemon.

        On Windows the daemon can take 2-6 minutes to initialise on the first
        `open` call.  By starting it eagerly in a background thread we give it
        a head-start before the first real tool call arrives.
        """
        def _run() -> None:
            try:
                cmd = [
                    self._executable,
                ]
                if not self._headless:
                    cmd.append("--headed")
                cmd.extend([
                    "open",
                    "about:blank",
                    "--json",
                    "--session-name",
                    self._session_name,
                ])
                env = self._browser_env()
                if self._use_shell:
                    subprocess.run(
                        " ".join(cmd),
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=_DAEMON_WARMUP_TIMEOUT_S,
                        shell=True,
                        env=env,
                    )
                else:
                    subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=_DAEMON_WARMUP_TIMEOUT_S,
                        env=env,
                    )
            except Exception:
                pass

        self._warmup_thread = threading.Thread(target=_run, daemon=True)
        self._warmup_thread.start()

    async def _start_browser(self, url: str | None = None) -> None:
        # If the daemon is already warm (background thread or previous session),
        # just open the target URL directly.
        try:
            await self._exec("tab", "--json", timeout=10.0)
            self._started = True
            await self._refresh_tabs()
            if url and url != "about:blank":
                await self._exec("open", url, "--json")
            return
        except Exception:
            pass

        # Cold-start: open will initialise the daemon. On Windows this can take
        # several minutes on the very first run, so we rely on the generous
        # timeout set in loop.py for agent_browser calls.
        args = ["open", url or "about:blank"]
        args.extend(["--json", "--session-name", self._session_name])
        result = await self._exec(*args)
        _ = self._parse_data(result.stdout)
        self._started = True
        await self._refresh_tabs()

    async def _refresh_tabs(self) -> None:
        try:
            result = await self._exec("tab", "--json")
            data = self._parse_json(result.stdout)
            raw_tabs = data.get("data", {}).get("tabs", [])
            self._tabs = []
            for i, t in enumerate(raw_tabs):
                self._tabs.append({
                    "index": i + 1,
                    "id": t.get("tabId", f"t{i+1}"),
                    "url": t.get("url", ""),
                    "title": t.get("title", ""),
                    "active": t.get("active", False),
                })
            # Find active tab
            for i, t in enumerate(self._tabs):
                if t.get("active"):
                    self._active_tab_index = i + 1
                    break
        except Exception:
            self._tabs = []

    async def _switch_to_newest_tab(self) -> None:
        """After a click that may open a new tab, switch to the newest tab."""
        await self._refresh_tabs()
        if len(self._tabs) > 1:
            newest = self._tabs[-1]
            tab_id = newest.get("id", f"t{len(self._tabs)}")
            await self._exec("tab", tab_id, "--json")
            self._active_tab_index = len(self._tabs)
            # SPA pages need time to render after tab switch
            await asyncio.sleep(1.5)
            await self._refresh_tabs()

    async def _take_screenshot(self, path_override: str | None = None, annotate: bool = False) -> str | None:
        try:
            path = Path(path_override) if path_override else _SCREENSHOT_DIR / f"ab_{int(time.time() * 1000)}.png"
            args = ["screenshot", str(path)]
            if annotate:
                args.append("--annotate")
            args.append("--json")
            result = await self._exec(*args)
            data = self._parse_data(result.stdout)
            # agent-browser may return path in data.path
            returned_path = data.get("path", str(path))
            if Path(returned_path).exists():
                with open(returned_path, "rb") as f:
                    return base64.b64encode(f.read()).decode("ascii")
        except Exception:
            pass
        return None

    async def _exec(self, *args: str, timeout: float | None = None) -> Any:
        """Run agent-browser CLI and return stdout/stderr.

        Uses subprocess.run in a thread-pool executor because
        asyncio.create_subprocess_* hangs indefinitely with the
        agent-browser native binary on Windows.
        """
        cmd_list = [self._executable]
        if not self._headless:
            cmd_list.append("--headed")
        cmd_list.extend(args)
        shell = self._use_shell
        print(f"[agent-browser exec] {' '.join(cmd_list)}", file=sys.stderr, flush=True)

        tout = timeout if timeout is not None else _DEFAULT_TIMEOUT_S

        env = self._browser_env()

        def _run() -> subprocess.CompletedProcess[str]:
            if shell:
                return subprocess.run(
                    " ".join(cmd_list),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=tout,
                    shell=True,
                    env=env,
                )
            return subprocess.run(
                cmd_list,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=tout,
                env=env,
            )

        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run),
            timeout=tout + 5.0,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            raise RuntimeError(
                f"agent-browser failed (exit {result.returncode}): {stderr or stdout}"
            )

        class _Result:
            pass
        r = _Result()
        r.stdout = stdout
        r.stderr = stderr
        r.returncode = result.returncode
        print(f"[agent-browser result] rc={result.returncode} stdout_len={len(stdout)} stderr_len={len(stderr)}", file=sys.stderr, flush=True)
        return r

    def _parse_json(self, text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}

    def _parse_data(self, text: str) -> dict[str, Any]:
        """Parse agent-browser JSON output and unwrap the 'data' field."""
        raw = self._parse_json(text)
        return raw.get("data", {}) if isinstance(raw, dict) else {}

    def _parse_snapshot(self, text: str) -> tuple[str, dict[str, Any]]:
        d = self._parse_data(text)
        snapshot_text = d.get("snapshot", "")
        refs = d.get("refs", {})
        return snapshot_text, refs
