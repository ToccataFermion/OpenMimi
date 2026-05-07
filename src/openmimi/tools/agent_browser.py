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
import time
from pathlib import Path
from typing import Any

from .base import ToolBase
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Operate a Chromium browser via agent-browser (Rust CLI). "
    "Core workflow: 1) Call action='snapshot' to get an accessibility tree with @eN refs; "
    "2) Use those refs in action='click' / 'type' / 'fill' / 'hover' / 'drag' via the 'ref' field; "
    "3) Call action='screenshot' when visual verification is needed. "
    "If no ref is known, use 'target_text' for semantic text matching. "
    "Navigation: action='navigate' with 'url'. "
    "Tabs: action='tab_list' or action='tab_switch' with 'tab_index' (1-based). "
    "Mouse: action='mouse' with 'mouse_action' (move/down/up/wheel) and coordinates. "
    "For multi-step atomic execution, use action='batch' with 'steps'."
)

_DEFAULT_TIMEOUT_S = 30.0
_SCREENSHOT_DIR = Path(tempfile.gettempdir()) / "agent_browser_screenshots"


class AgentBrowserTool(ToolBase):
    name = "agent_browser"

    def __init__(
        self,
        *,
        download_dir: str,
        viewport: tuple[int, int] = (1280, 800),
        headless: bool = False,
        executable: str = "agent-browser",
    ) -> None:
        self._download_dir = Path(download_dir)
        self._viewport = viewport
        self._headless = headless
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
        self._use_shell = sys.platform == "win32" and self._executable.lower().endswith((".cmd", ".bat"))

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
                            "snapshot",
                            "click",
                            "type",
                            "fill",
                            "press",
                            "hover",
                            "scroll",
                            "screenshot",
                            "extract",
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
            "snapshot": self._do_snapshot,
            "click": self._do_click,
            "type": self._do_type,
            "fill": self._do_fill,
            "press": self._do_press,
            "hover": self._do_hover,
            "scroll": self._do_scroll,
            "screenshot": self._do_screenshot,
            "extract": self._do_extract,
            "tab_list": self._do_tab_list,
            "tab_switch": self._do_tab_switch,
            "tab_new": self._do_tab_new,
            "tab_close": self._do_tab_close,
            "wait": self._do_wait,
            "eval": self._do_eval,
            "batch": self._do_batch,
            "drag": self._do_drag,
            "mouse": self._do_mouse,
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

    async def _do_snapshot(self, inp: dict[str, Any]) -> ToolResult:
        snapshot = await self._exec("snapshot", "--json")
        text, refs = self._parse_snapshot(snapshot.stdout)
        image = await self._take_screenshot()
        details = {
            "open_tabs": self._tabs,
            "active_tab": self._active_tab_index,
            "refs": refs,
        }
        return ToolResult(
            output=f"Snapshot:\n{text}",
            base64_image=image,
            details=details,
        )

    async def _do_click(self, inp: dict[str, Any]) -> ToolResult:
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        if ref:
            result = await self._exec("click", ref, "--json")
        elif target_text:
            result = await self._exec(
                "find", "text", target_text, "click", "--json"
            )
        else:
            return ToolResult(output="click requires 'ref' or 'target_text'")
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
        image = await self._take_screenshot()
        return ToolResult(output="Screenshot taken", base64_image=image)

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
        data = self._parse_data(result.stdout)
        return ToolResult(output=json.dumps(data.get("result"), ensure_ascii=False, indent=2))

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

    async def _do_close(self) -> ToolResult:
        if self._started:
            await self._exec("close", "--all", "--json")
            self._started = False
        return ToolResult(output="Browser closed.")

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    async def _start_browser(self, url: str | None = None) -> None:
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

    async def _take_screenshot(self) -> str | None:
        try:
            path = _SCREENSHOT_DIR / f"ab_{int(time.time() * 1000)}.png"
            result = await self._exec("screenshot", str(path), "--json")
            data = self._parse_data(result.stdout)
            # agent-browser may return path in data.path
            returned_path = data.get("path", str(path))
            if Path(returned_path).exists():
                with open(returned_path, "rb") as f:
                    return base64.b64encode(f.read()).decode("ascii")
        except Exception:
            pass
        return None

    async def _exec(self, *args: str) -> Any:
        """Run agent-browser CLI and return stdout/stderr.

        Uses subprocess.run in a thread-pool executor because
        asyncio.create_subprocess_* hangs indefinitely with the
        agent-browser native binary on Windows.
        """
        cmd_list = [self._executable] + list(args)
        shell = self._use_shell
        print(f"[agent-browser exec] {' '.join(cmd_list)}", file=sys.stderr, flush=True)

        def _run() -> subprocess.CompletedProcess[str]:
            if shell:
                return subprocess.run(
                    " ".join(cmd_list),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_DEFAULT_TIMEOUT_S,
                    shell=True,
                )
            return subprocess.run(
                cmd_list,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_DEFAULT_TIMEOUT_S,
            )

        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run),
            timeout=_DEFAULT_TIMEOUT_S + 5.0,
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
