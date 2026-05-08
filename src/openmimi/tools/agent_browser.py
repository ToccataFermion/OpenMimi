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
    "Scroll into view: action='scroll_into_view' with 'ref' or 'target_text' brings an element into the viewport. "
    "Page source: action='page_source' returns the raw HTML of the current page. "
    "Wait for navigation: action='wait_for_navigation' waits for the URL to change after a click or form submission. "
    "Element coordinates: action='get_box' with 'ref' or 'target_text' returns the "
    "element's bounding box (x, y, width, height) for OS-level mouse coordination. "
    "Dynamic content: action='wait_for' with 'ref', 'target_text', or 'text' waits until "
    "the element or text appears on the page (useful for React/Vue SPAs that render lazily). "
    "Network debugging: action='network_log' with optional 'duration_ms' and 'filter' "
    "intercepts fetch/XHR requests to discover hidden API endpoints. "
    "Network modification: action='network_modify' with 'modify_action' can inject headers, "
    "block URLs by pattern, mock responses, or override User-Agent. Use this to bypass "
    "anti-bot detection or inject auth tokens into API requests. "
    "Extract: action='extract' with 'instruction' retrieves structured data: 'get text', "
    "'headings', 'links', 'forms', 'tables', 'metadata', 'images'.\n"
    "Storage: action='storage' with 'storage_action' (get/set/delete/clear) and 'storage_type' "
    "(localStorage, sessionStorage, cookies) to read or modify browser storage. "
    "PDF: action='pdf' with 'file_path' saves the current page as a PDF. "
    "Console: action='console' returns recent browser console logs (errors, warnings, info). "
    "Clear data: action='clear_cache' wipes cookies, localStorage, and sessionStorage. "
    "Viewport: action='set_viewport' with 'width' and 'height' resizes the browser window. "
    "Device emulation: action='emulate_device' with 'device_name' (iPhone 14, Pixel 7, iPad Mini, reset) "
    "sets viewport, DPR, and user agent for mobile testing.\n"
    "Session persistence: action='save_session' with 'file_path' persists cookies/storage; "
    "action='load_session' with 'file_path' restores them to avoid repeated logins. "
    "Persistent profile: pass user_data_dir when creating the tool to reuse cookies, cache, "
    "and extensions across sessions (more robust than save_session/load_session). "
    "Proxy: pass proxy='http://host:port' when creating the tool to route traffic through a proxy.\n"
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

# JavaScript to inject after navigation when stealth mode is enabled.
# Masks the most common automation indicators that sites check for.
_STEALTH_JS = """
(() => {
    if (window.__openmimi_stealth_applied) return;
    window.__openmimi_stealth_applied = true;

    const _defineProp = (obj, prop, getter) => {
        try {
            Object.defineProperty(obj, prop, { get: getter, configurable: true });
        } catch (e) {}
    };

    // 1. navigator.webdriver
    _defineProp(navigator, 'webdriver', () => undefined);

    // 2. Plugins + MimeTypes (with proper array-like behavior)
    const makeFakePlugins = () => {
        const plugins = [
            {name: "Chrome PDF Plugin", filename: "internal-pdf-viewer", description: "Portable Document Format", version: "undefined", length: 1, item: () => null, namedItem: () => null},
            {name: "Chrome PDF Viewer", filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai", description: "Portable Document Format", version: "undefined", length: 1, item: () => null, namedItem: () => null},
            {name: "Native Client", filename: "internal-nacl-plugin", description: "", version: "undefined", length: 2, item: () => null, namedItem: () => null},
        ];
        plugins.length = plugins.length;
        plugins.item = (i) => plugins[i];
        plugins.namedItem = (n) => plugins.find(p => p.name === n) || null;
        plugins.refresh = () => {};
        return plugins;
    };
    const makeFakeMimeTypes = () => {
        const mimes = [
            {type: "application/pdf", suffixes: "pdf", description: "", enabledPlugin: makeFakePlugins()[0]},
            {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format", enabledPlugin: makeFakePlugins()[1]},
            {type: "application/x-nacl", suffixes: "", description: "", enabledPlugin: makeFakePlugins()[2]},
            {type: "application/x-pnacl", suffixes: "", description: "", enabledPlugin: makeFakePlugins()[2]},
        ];
        mimes.length = mimes.length;
        mimes.item = (i) => mimes[i];
        mimes.namedItem = (n) => mimes.find(m => m.type === n) || null;
        return mimes;
    };
    _defineProp(navigator, 'plugins', makeFakePlugins);
    _defineProp(navigator, 'mimeTypes', makeFakeMimeTypes);

    // 3. Languages
    _defineProp(navigator, 'languages', () => ['zh-CN', 'zh', 'en-US', 'en']);

    // 4. Permissions (allow everything common)
    const origQuery = navigator.permissions?.query;
    if (origQuery) {
        navigator.permissions.query = function(parameters) {
            const name = parameters?.name;
            if (['notifications', 'midi', 'midi-sysex', 'push', 'camera', 'microphone', 'background-sync', 'ambient-light-sensor', 'accelerometer', 'gyroscope', 'magnetometer', 'clipboard-read', 'clipboard-write', 'payment-handler', 'idle-detection', 'storage-access'].includes(name)) {
                return Promise.resolve({ state: 'prompt', onchange: null });
            }
            return origQuery.call(this, parameters);
        };
    }

    // 5. Chrome runtime (more complete)
    if (!window.chrome) window.chrome = {};
    _defineProp(window.chrome, 'runtime', () => ({
        OnInstalledReason: {CHROME_UPDATE: "chrome_update", BROWSER_UPDATE: "browser_update", SHARED_MODULE_UPDATE: "shared_module_update"},
        OnRestartRequiredReason: {APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic"},
        PlatformArch: {ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", MIPS64EL: "mips64el", X86_32: "x86-32", X86_64: "x86-64"},
        PlatformNaclArch: {ARM: "arm", MIPS: "mips", MIPS64: "mips64", MIPS64EL: "mips64el", X86_32: "x86-32", X86_64: "x86-64"},
        PlatformOs: {ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win"},
        RequestUpdateCheckStatus: {NO_UPDATE: "no_update", THROTTLED: "throttled", UPDATE_AVAILABLE: "update_available"},
        connect: () => ({postMessage: () => {}, disconnect: () => {}}),
        sendMessage: () => {},
        onMessage: {addListener: () => {}, removeListener: () => {}},
        onConnect: {addListener: () => {}, removeListener: () => {}},
    }));
    _defineProp(window.chrome, 'app', () => ({isInstalled: false, InstallState: {DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed"}, RunningState: {CANNOT_RUN: "cannot_run", READY_TO_RUN: "ready_to_run", RUNNING: "running"}}));

    // 6. WebGL vendor/renderer
    const getParamProxy = (target) => {
        return new Proxy(target.getParameter, {
            apply: function(target, thisArg, args) {
                const pname = args[0];
                if (pname === 37445) return 'Intel Inc.';           // UNMASKED_VENDOR_WEBGL
                if (pname === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
                return Reflect.apply(target, thisArg, args);
            }
        });
    };
    const hookWebGL = (proto) => {
        if (!proto) return;
        const origGetParam = proto.getParameter;
        proto.getParameter = function(pname) {
            if (pname === 37445) return 'Intel Inc.';
            if (pname === 37446) return 'Intel Iris OpenGL Engine';
            return origGetParam.call(this, pname);
        };
    };
    try {
        hookWebGL(WebGLRenderingContext?.prototype);
        hookWebGL(WebGL2RenderingContext?.prototype);
    } catch (e) {}

    // 7. Canvas fingerprint consistency (add subtle noise)
    const hookCanvas = (name) => {
        const orig = HTMLCanvasElement.prototype[name];
        HTMLCanvasElement.prototype[name] = function(...args) {
            const result = orig.apply(this, args);
            if (result && result.getContext) {
                const getCtx = result.getContext;
                result.getContext = function(type, attrs) {
                    const ctx = getCtx.call(this, type, attrs);
                    if (ctx && (type === 'webgl' || type === 'experimental-webgl')) {
                        hookWebGL(ctx.__proto__);
                    }
                    return ctx;
                };
            }
            return result;
        };
    };

    // 8. Battery API (some sites check for missing battery)
    if (!navigator.getBattery) {
        navigator.getBattery = () => Promise.resolve({
            charging: true, chargingTime: 0, dischargingTime: Infinity, level: 1,
            addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true,
        });
    }

    // 9. Memory info
    _defineProp(navigator, 'deviceMemory', () => 8);
    _defineProp(navigator, 'hardwareConcurrency', () => 8);

    // 10. Connection info
    _defineProp(navigator, 'connection', () => ({
        effectiveType: '4g', rtt: 50, downlink: 10, saveData: false,
        addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true,
    }));

    // 11. Keyboard layout
    _defineProp(navigator, 'keyboard', () => ({ getLayoutMap: () => Promise.resolve({get: () => undefined}) }));

    // 12. document.documentElement webdriver cleanup
    const origDocElem = Object.getOwnPropertyDescriptor(Document.prototype, 'documentElement');
    if (origDocElem) {
        Object.defineProperty(Document.prototype, 'documentElement', {
            get: function() {
                const el = origDocElem.get.call(this);
                if (el && el.hasAttribute && el.hasAttribute('webdriver')) {
                    el.removeAttribute('webdriver');
                }
                return el;
            },
            configurable: true,
        });
    }

    // 13. Iframe inheritance: apply stealth to new iframes
    const origCreateElement = Document.prototype.createElement;
    Document.prototype.createElement = function(...args) {
        const el = origCreateElement.apply(this, args);
        if (el && el.tagName === 'IFRAME') {
            el.addEventListener('load', function() {
                try {
                    const d = el.contentDocument || el.contentWindow?.document;
                    if (d && !d.__openmimi_stealth_applied) {
                        d.__openmimi_stealth_applied = true;
                        _defineProp(d.navigator || navigator, 'webdriver', () => undefined);
                    }
                } catch (e) {}
            });
        }
        return el;
    };

    // 14. Notification permission
    if (window.Notification) {
        _defineProp(Notification, 'permission', () => 'default');
    }
})()
"""


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
        stealth: bool = True,
        proxy: str | None = None,
        user_data_dir: str | None = None,
    ) -> None:
        self._download_dir = Path(download_dir)
        self._viewport = viewport
        self._headless = headless
        self._stealth = stealth
        self._proxy = proxy
        self._user_data_dir = Path(user_data_dir) if user_data_dir else None
        self._browser_args = browser_args or []
        # Add default stealth args if stealth mode is on
        if self._stealth:
            _default_stealth_args = [
                "--disable-blink-features=AutomationControlled",
            ]
            for arg in _default_stealth_args:
                if arg not in self._browser_args:
                    self._browser_args.insert(0, arg)
        # Proxy support
        if self._proxy:
            proxy_arg = f"--proxy-server={self._proxy}"
            if proxy_arg not in self._browser_args:
                self._browser_args.append(proxy_arg)
        # Persistent profile (cookies, cache, extensions, IndexedDB)
        if self._user_data_dir:
            udd_arg = f"--user-data-dir={self._user_data_dir}"
            if udd_arg not in self._browser_args:
                self._browser_args.append(udd_arg)
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
                            "network_log",
                            "network_modify",
                            "storage",
                            "pdf",
                            "console",
                            "clear_cache",
                            "set_viewport",
                            "save_session",
                            "load_session",
                            "scroll_into_view",
                            "page_source",
                            "wait_for_navigation",
                            "emulate_device",
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
                    "duration_ms": {
                        "type": "integer",
                        "description": "Duration in milliseconds to capture network traffic (action='network_log', default 5000).",
                    },
                    "filter": {
                        "type": "string",
                        "description": "Filter string for network_log to only show URLs/requests containing this text.",
                    },
                    "modify_action": {
                        "type": "string",
                        "enum": ["inject_headers", "block_urls", "mock_response", "user_agent", "clear"],
                        "description": "Modification type for action='network_modify'.",
                    },
                    "headers": {
                        "type": "object",
                        "description": "Headers to inject for network_modify inject_headers (key-value pairs).",
                    },
                    "url_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "URL patterns to block or mock for action='network_modify'.",
                    },
                    "mock_data": {
                        "type": "object",
                        "description": "Mock response data for network_modify mock_response: {status, body, headers}.",
                    },
                    "user_agent": {
                        "type": "string",
                        "description": "Custom User-Agent string for network_modify user_agent.",
                    },
                    "storage_action": {
                        "type": "string",
                        "enum": ["get", "set", "delete", "clear", "list"],
                        "description": "Storage operation for action='storage' (default: get).",
                    },
                    "storage_type": {
                        "type": "string",
                        "enum": ["localStorage", "sessionStorage", "cookies"],
                        "description": "Storage target for action='storage' (default: localStorage).",
                    },
                    "storage_key": {
                        "type": "string",
                        "description": "Key for storage get/set/delete.",
                    },
                    "storage_value": {
                        "type": "string",
                        "description": "Value for storage set.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Target path for action='pdf' (save page as PDF) or action='download'.",
                    },
                    "console_level": {
                        "type": "string",
                        "enum": ["all", "error", "warn", "info", "log"],
                        "description": "Filter level for action='console' (default: all).",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Viewport width in pixels for action='set_viewport'.",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Viewport height in pixels for action='set_viewport'.",
                    },
                    "behavior": {
                        "type": "string",
                        "enum": ["smooth", "auto", "instant"],
                        "description": "Scroll behavior for action='scroll_into_view' (default: smooth).",
                    },
                    "block": {
                        "type": "string",
                        "enum": ["start", "center", "end", "nearest"],
                        "description": "Vertical alignment for action='scroll_into_view' (default: center).",
                    },
                    "include_html": {
                        "type": "boolean",
                        "description": "For action='page_source': include raw HTML in output (default true).",
                    },
                    "expected_url": {
                        "type": "string",
                        "description": "For action='wait_for_navigation': substring expected in the URL after navigation.",
                    },
                    "device_name": {
                        "type": "string",
                        "enum": ["iPhone 14", "iPhone 14 Pro Max", "Pixel 7", "iPad Mini", "reset"],
                        "description": "Device preset for action='emulate_device'. Use 'reset' to restore desktop.",
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
            "network_log": self._do_network_log,
            "network_modify": self._do_network_modify,
            "storage": self._do_storage,
            "pdf": self._do_pdf,
            "console": self._do_console,
            "clear_cache": self._do_clear_cache,
            "set_viewport": self._do_set_viewport,
            "save_session": self._do_save_session,
            "load_session": self._do_load_session,
            "scroll_into_view": self._do_scroll_into_view,
            "page_source": self._do_page_source,
            "wait_for_navigation": self._do_wait_for_navigation,
            "emulate_device": self._do_emulate_device,
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

    async def _do_scroll_into_view(self, inp: dict[str, Any]) -> ToolResult:
        """Scroll an element into view using JS scrollIntoView."""
        ref = inp.get("ref")
        target_text = inp.get("target_text")
        behavior = inp.get("behavior", "smooth")
        block = inp.get("block", "center")
        if not ref and not target_text:
            return ToolResult(
                output="scroll_into_view requires 'ref' or 'target_text'", is_error=True
            )
        if ref:
            js = f"""
            (() => {{
                const el = document.querySelector({json.dumps(ref.lstrip('@'))});
                if (!el) return {{error: 'element not found'}};
                el.scrollIntoView({{behavior: {json.dumps(behavior)}, block: {json.dumps(block)}}});
                return {{ok: true, tag: el.tagName, text: (el.innerText || '').trim().substring(0, 40)}};
            }})()
            """
        else:
            js = f"""
            (() => {{
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
                let el;
                while (el = walker.nextNode()) {{
                    if ((el.innerText || el.textContent || '').trim().includes({json.dumps(target_text)})) {{
                        el.scrollIntoView({{behavior: {json.dumps(behavior)}, block: {json.dumps(block)}}});
                        return {{ok: true, tag: el.tagName, text: (el.innerText || '').trim().substring(0, 40)}};
                    }}
                }}
                return {{error: 'element not found'}};
            }})()
            """
        try:
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            if isinstance(result_value, dict) and result_value.get("error"):
                return ToolResult(
                    output=f"scroll_into_view failed: {result_value['error']}", is_error=True
                )
            image = await self._take_screenshot()
            return ToolResult(
                output=f"Scrolled into view: {json.dumps(result_value, ensure_ascii=False)[:200]}",
                base64_image=image,
            )
        except Exception as exc:
            return ToolResult(output=f"scroll_into_view error: {exc}", is_error=True)

    async def _do_page_source(self, inp: dict[str, Any]) -> ToolResult:
        """Return the raw HTML source of the current page."""
        include_html = inp.get("include_html", True)
        js = "(() => ({title: document.title, url: window.location.href, html: document.documentElement.outerHTML}))()"
        try:
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            if not isinstance(result_value, dict):
                return ToolResult(output="page_source failed: unexpected response", is_error=True)
            title = result_value.get("title", "")
            url = result_value.get("url", "")
            html = result_value.get("html", "")
            output = f"Title: {title}\nURL: {url}\n"
            if include_html:
                output += f"\nHTML source ({len(html)} chars):\n```html\n{html[:6000]}\n```"
                if len(html) > 6000:
                    output += "\n... [truncated]"
            return ToolResult(
                output=output,
                details={"title": title, "url": url, "html_length": len(html)},
            )
        except Exception as exc:
            return ToolResult(output=f"page_source error: {exc}", is_error=True)

    async def _do_wait_for_navigation(self, inp: dict[str, Any]) -> ToolResult:
        """Wait for the page URL to change, indicating navigation has occurred."""
        expected_url = inp.get("expected_url", "")
        timeout_ms = inp.get("timeout_ms", 10000)
        interval_ms = inp.get("interval_ms", 500)
        try:
            result = await self._exec("eval", "(() => window.location.href)()", "--json")
            data = self._parse_data(result.stdout)
            start_url = data.get("result") if isinstance(data, dict) else ""
        except Exception:
            start_url = ""
        start = time.monotonic()
        while (time.monotonic() - start) * 1000 < timeout_ms:
            try:
                result = await self._exec("eval", "(() => window.location.href)()", "--json")
                data = self._parse_data(result.stdout)
                current_url = data.get("result") if isinstance(data, dict) else ""
                if current_url != start_url:
                    if not expected_url or expected_url in current_url:
                        image = await self._take_screenshot()
                        return ToolResult(
                            output=f"Navigation detected: {start_url} -> {current_url}",
                            base64_image=image,
                            details={"previous_url": start_url, "current_url": current_url},
                        )
            except Exception:
                pass
            await asyncio.sleep(interval_ms / 1000.0)
        return ToolResult(
            output=f"wait_for_navigation timed out after {timeout_ms}ms (URL did not change from {start_url})",
            is_error=True,
        )

    async def _do_emulate_device(self, inp: dict[str, Any]) -> ToolResult:
        """Emulate a mobile device via CDP or JS fallback."""
        device_name = inp.get("device_name", "iPhone 14")
        _DEVICE_PRESETS: dict[str, dict[str, Any]] = {
            "iPhone 14": {"width": 390, "height": 844, "deviceScaleFactor": 3, "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1", "mobile": True, "touch": True},
            "iPhone 14 Pro Max": {"width": 430, "height": 932, "deviceScaleFactor": 3, "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1", "mobile": True, "touch": True},
            "Pixel 7": {"width": 412, "height": 915, "deviceScaleFactor": 2.625, "userAgent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36", "mobile": True, "touch": True},
            "iPad Mini": {"width": 768, "height": 1024, "deviceScaleFactor": 2, "userAgent": "Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1", "mobile": True, "touch": True},
            "reset": {"width": 1280, "height": 800, "deviceScaleFactor": 1, "userAgent": "", "mobile": False, "touch": False},
        }
        preset = _DEVICE_PRESETS.get(device_name, _DEVICE_PRESETS["iPhone 14"])
        width = int(preset["width"])
        height = int(preset["height"])
        dpr = float(preset["deviceScaleFactor"])
        ua = preset["userAgent"]
        mobile = preset["mobile"]
        touch = preset["touch"]

        # Try CDP Emulation.setDeviceMetricsOverride first
        cdp_js = f"""
        (async () => {{
            try {{
                await window.__openmimi_cdp_send('Emulation.setDeviceMetricsOverride', {{
                    width: {width},
                    height: {height},
                    deviceScaleFactor: {dpr},
                    mobile: {str(mobile).lower()},
                }});
                {'await window.__openmimi_cdp_send("Emulation.setUserAgentOverride", {userAgent: ' + json.dumps(ua) + '});' if ua else ''}
                return {{ok: true, method: 'cdp', device: {json.dumps(device_name)}}};
            }} catch (e) {{
                return {{error: e.message, note: 'CDP emulation failed'}};
            }}
        }})()
        """
        try:
            result = await self._exec("eval", cdp_js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            if isinstance(result_value, dict) and result_value.get("ok"):
                image = await self._take_screenshot()
                return ToolResult(
                    output=f"Emulating {device_name}: {width}x{height} @ {dpr}x DPR",
                    base64_image=image,
                    details={"device": device_name, "width": width, "height": height, "dpr": dpr},
                )
        except Exception:
            pass

        # Fallback: JS-only viewport + UA override
        js = f"""
        (() => {{
            window.resizeTo({width}, {height});
            {'Object.defineProperty(navigator, "userAgent", {get: () => ' + json.dumps(ua) + ', configurable: true});' if ua else ''}
            return {{
                width: window.innerWidth,
                height: window.innerHeight,
                device: {json.dumps(device_name)},
                method: 'js_fallback',
            }};
        }})()
        """
        try:
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            image = await self._take_screenshot()
            return ToolResult(
                output=f"Emulating {device_name} (JS fallback): {json.dumps(result_value, ensure_ascii=False)[:500]}",
                base64_image=image,
                details={"device": device_name, "width": width, "height": height, "dpr": dpr},
            )
        except Exception as exc:
            return ToolResult(output=f"emulate_device failed: {exc}", is_error=True)

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
        """Extract structured data from the page based on instruction."""
        instruction = inp.get("instruction", "get text")

        if instruction == "get text":
            result = await self._exec(
                "eval", "document.body.innerText", "--json"
            )
            data = self._parse_data(result.stdout)
            text = data.get("result", "")
            return ToolResult(output=text[:4000])

        if instruction == "headings":
            js = """
            (() => {
                const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'));
                return headings.map(h => ({level: parseInt(h.tagName[1]), text: h.innerText.trim().substring(0, 200)}));
            })()
            """
        elif instruction == "links":
            js = """
            (() => {
                const links = Array.from(document.querySelectorAll('a[href]'));
                return links.map(a => ({text: (a.innerText || a.textContent).trim().substring(0, 100), href: a.href})).filter(l => l.text || l.href);
            })()
            """
        elif instruction == "forms":
            js = """
            (() => {
                const forms = Array.from(document.querySelectorAll('form'));
                return forms.map((f, i) => ({
                    index: i,
                    action: f.action || null,
                    method: f.method || 'get',
                    inputs: Array.from(f.querySelectorAll('input, select, textarea, button')).map(el => ({
                        tag: el.tagName.toLowerCase(),
                        type: el.type || null,
                        name: el.name || null,
                        id: el.id || null,
                        placeholder: el.placeholder || null,
                        value: el.value ? String(el.value).substring(0, 100) : null,
                    }))
                }));
            })()
            """
        elif instruction == "tables":
            js = """
            (() => {
                const tables = Array.from(document.querySelectorAll('table'));
                return tables.map((t, i) => {
                    const rows = Array.from(t.querySelectorAll('tr'));
                    return {
                        index: i,
                        rows: rows.map(r => Array.from(r.querySelectorAll('td, th')).map(c => c.innerText.trim().substring(0, 200)))
                    };
                });
            })()
            """
        elif instruction == "metadata":
            js = """
            (() => {
                const meta = {};
                document.querySelectorAll('meta').forEach(m => {
                    const name = m.getAttribute('name') || m.getAttribute('property') || m.getAttribute('http-equiv');
                    if (name) meta[name] = m.getAttribute('content');
                });
                return {
                    title: document.title,
                    url: window.location.href,
                    description: meta.description || null,
                    keywords: meta.keywords || null,
                    ogTitle: meta['og:title'] || null,
                    ogDescription: meta['og:description'] || null,
                    ogImage: meta['og:image'] || null,
                    meta: meta,
                };
            })()
            """
        elif instruction == "images":
            js = """
            (() => {
                const imgs = Array.from(document.querySelectorAll('img'));
                return imgs.map((img, i) => ({
                    index: i,
                    src: img.src || null,
                    alt: img.alt || null,
                    width: img.naturalWidth || img.width || null,
                    height: img.naturalHeight || img.height || null,
                })).filter(img => img.src);
            })()
            """
        else:
            # Generic extraction via eval
            js = f"({{url: window.location.href, title: document.title, text: document.body.innerText}})"

        try:
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            output = json.dumps(result_value, ensure_ascii=False, indent=2)
            return ToolResult(output=output[:4000], details={"instruction": instruction})
        except Exception as exc:
            return ToolResult(output=f"extract failed: {exc}", is_error=True)

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
        # If result is null/undefined/empty, return more context so the caller
        # can diagnose whether the JS ran but had no return, or something else.
        if result_value is None or result_value == {} or result_value == []:
            return ToolResult(
                output=(
                    f"eval result is empty/null. "
                    f"Make sure your JS ends with an explicit return value "
                    f"inside an IIFE like (() => {{ ...; return value; }})(). "
                    f"Raw data: {json.dumps(data, ensure_ascii=False)[:500]}"
                )
            )
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

    async def _do_network_log(self, inp: dict[str, Any]) -> ToolResult:
        """Inject JS to intercept network requests and return captured traffic."""
        duration_ms = inp.get("duration_ms", 5000)
        filter_text = inp.get("filter", "")
        js = f"""
            (() => {{
                const captured = window.__openmimi_captured_requests || [];
                let filtered = captured;
                const filter = {json.dumps(filter_text)};
                if (filter) {{
                    filtered = captured.filter(r =>
                        r.url.includes(filter) ||
                        (r.body && r.body.includes(filter))
                    );
                }}
                return {{
                    count: filtered.length,
                    requests: filtered.slice(-20)
                }};
            }})()
        """
        # First, ensure the interceptor is installed
        setup_js = """
            (() => {
                if (window.__openmimi_network_hooked) return {ok: true, already: true};
                window.__openmimi_captured_requests = [];
                const origFetch = window.fetch;
                window.fetch = function(...args) {
                    const url = args[0] instanceof Request ? args[0].url : String(args[0]);
                    const options = args[1] || {};
                    window.__openmimi_captured_requests.push({
                        type: 'fetch',
                        url: url,
                        method: options.method || 'GET',
                        body: options.body ? String(options.body).substring(0, 500) : null,
                        time: Date.now(),
                    });
                    if (window.__openmimi_captured_requests.length > 200) {
                        window.__openmimi_captured_requests.shift();
                    }
                    return origFetch.apply(this, args);
                };
                const origXHR = window.XMLHttpRequest.prototype.open;
                const origXHRSend = window.XMLHttpRequest.prototype.send;
                window.XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                    this._om_method = method;
                    this._om_url = url;
                    return origXHR.call(this, method, url, ...rest);
                };
                window.XMLHttpRequest.prototype.send = function(body) {
                    window.__openmimi_captured_requests.push({
                        type: 'xhr',
                        url: this._om_url,
                        method: this._om_method || 'GET',
                        body: body ? String(body).substring(0, 500) : null,
                        time: Date.now(),
                    });
                    if (window.__openmimi_captured_requests.length > 200) {
                        window.__openmimi_captured_requests.shift();
                    }
                    return origXHRSend.call(this, body);
                };
                window.__openmimi_network_hooked = true;
                return {ok: true, already: false};
            })()
        """
        try:
            setup_result = await self._exec("eval", setup_js, "--json")
            setup_data = self._parse_data(setup_result.stdout)
            if duration_ms > 0:
                await asyncio.sleep(duration_ms / 1000.0)
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            requests = data.get("requests", []) if isinstance(data, dict) else []
            return ToolResult(
                output=json.dumps(data, ensure_ascii=False, indent=2)[:4000],
                details={"requests": requests},
            )
        except Exception as exc:
            return ToolResult(
                output=f"network_log failed: {exc}", is_error=True
            )

    async def _do_network_modify(self, inp: dict[str, Any]) -> ToolResult:
        """Modify network behavior via JS patching or CDP: inject headers, block URLs,
        mock responses, or set User-Agent."""
        modify_action = inp.get("modify_action", "inject_headers")

        if modify_action == "user_agent":
            ua = inp.get("user_agent", "")
            if not ua:
                return ToolResult(output="network_modify user_agent requires 'user_agent'", is_error=True)
            # Try CDP first, fallback to JS navigator override
            cdp_js = f"""
            (async () => {{
                try {{
                    await window.__openmimi_cdp_send('Network.setUserAgentOverride', {{userAgent: {json.dumps(ua)}}});
                    return {{ok: true, method: 'cdp'}};
                }} catch (e) {{
                    Object.defineProperty(navigator, 'userAgent', {{
                        get: () => {json.dumps(ua)},
                        configurable: true,
                    }});
                    return {{ok: true, method: 'js', note: 'CDP failed, used JS override'}};
                }}
            }})()
            """
            try:
                result = await self._exec("eval", cdp_js, "--json")
                data = self._parse_data(result.stdout)
                result_value = data.get("result") if isinstance(data, dict) else None
                return ToolResult(
                    output=f"User-Agent set to: {ua[:80]}...\n{json.dumps(result_value, ensure_ascii=False)[:500]}",
                    details={"user_agent": ua},
                )
            except Exception as exc:
                return ToolResult(output=f"user_agent modify failed: {exc}", is_error=True)

        if modify_action == "inject_headers":
            headers = inp.get("headers", {})
            if not headers:
                return ToolResult(output="network_modify inject_headers requires 'headers'", is_error=True)
            headers_json = json.dumps(headers, ensure_ascii=False)
            js = f"""
            (() => {{
                const customHeaders = {headers_json};
                if (!window.__openmimi_orig_fetch) {{
                    window.__openmimi_orig_fetch = window.fetch;
                }}
                window.fetch = function(resource, init) {{
                    init = init || {{}};
                    init.headers = init.headers || {{}};
                    if (init.headers instanceof Headers) {{
                        for (const [k, v] of Object.entries(customHeaders)) {{
                            init.headers.set(k, v);
                        }}
                    }} else {{
                        Object.assign(init.headers, customHeaders);
                    }}
                    return window.__openmimi_orig_fetch(resource, init);
                }};
                if (!window.__openmimi_orig_xhr_open) {{
                    window.__openmimi_orig_xhr_open = window.XMLHttpRequest.prototype.open;
                    window.__openmimi_orig_xhr_send = window.XMLHttpRequest.prototype.send;
                }}
                window.XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
                    this._om_method = method;
                    this._om_url = url;
                    this._om_headers = {{}};
                    return window.__openmimi_orig_xhr_open.call(this, method, url, ...rest);
                }};
                const origSetRequestHeader = window.XMLHttpRequest.prototype.setRequestHeader;
                window.XMLHttpRequest.prototype.setRequestHeader = function(header, value) {{
                    this._om_headers[header] = value;
                    return origSetRequestHeader.call(this, header, value);
                }};
                window.XMLHttpRequest.prototype.send = function(body) {{
                    for (const [k, v] of Object.entries(customHeaders)) {{
                        if (!this._om_headers[k]) {{
                            origSetRequestHeader.call(this, k, v);
                        }}
                    }}
                    return window.__openmimi_orig_xhr_send.call(this, body);
                }};
                return {{ok: true, headers: customHeaders}};
            }})()
            """
            try:
                result = await self._exec("eval", js, "--json")
                data = self._parse_data(result.stdout)
                result_value = data.get("result") if isinstance(data, dict) else None
                return ToolResult(
                    output=f"Headers injected: {json.dumps(result_value, ensure_ascii=False)[:500]}",
                    details={"headers": headers},
                )
            except Exception as exc:
                return ToolResult(output=f"inject_headers failed: {exc}", is_error=True)

        if modify_action == "block_urls":
            patterns = inp.get("url_patterns", [])
            if not patterns:
                return ToolResult(output="network_modify block_urls requires 'url_patterns'", is_error=True)
            patterns_json = json.dumps(patterns, ensure_ascii=False)
            js = f"""
            (() => {{
                const patterns = {patterns_json};
                if (!window.__openmimi_orig_fetch) {{
                    window.__openmimi_orig_fetch = window.fetch;
                }}
                window.fetch = function(resource, init) {{
                    const url = (resource instanceof Request) ? resource.url : String(resource);
                    for (const p of patterns) {{
                        if (url.includes(p)) {{
                            return Promise.reject(new TypeError('Blocked by OpenMimi: ' + p));
                        }}
                    }}
                    return window.__openmimi_orig_fetch(resource, init);
                }};
                if (!window.__openmimi_orig_xhr_open) {{
                    window.__openmimi_orig_xhr_open = window.XMLHttpRequest.prototype.open;
                    window.__openmimi_orig_xhr_send = window.XMLHttpRequest.prototype.send;
                }}
                window.XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
                    this._om_method = method;
                    this._om_url = url;
                    return window.__openmimi_orig_xhr_open.call(this, method, url, ...rest);
                }};
                window.XMLHttpRequest.prototype.send = function(body) {{
                    for (const p of patterns) {{
                        if (this._om_url && this._om_url.includes(p)) {{
                            this.dispatchEvent(new Event('error'));
                            return;
                        }}
                    }}
                    return window.__openmimi_orig_xhr_send.call(this, body);
                }};
                return {{ok: true, blocked: patterns}};
            }})()
            """
            try:
                result = await self._exec("eval", js, "--json")
                data = self._parse_data(result.stdout)
                result_value = data.get("result") if isinstance(data, dict) else None
                return ToolResult(
                    output=f"URL patterns blocked: {json.dumps(result_value, ensure_ascii=False)[:500]}",
                    details={"blocked_patterns": patterns},
                )
            except Exception as exc:
                return ToolResult(output=f"block_urls failed: {exc}", is_error=True)

        if modify_action == "mock_response":
            mock_data = inp.get("mock_data", {})
            url_patterns = inp.get("url_patterns", [])
            if not url_patterns or not mock_data:
                return ToolResult(output="network_modify mock_response requires 'url_patterns' and 'mock_data'", is_error=True)
            patterns_json = json.dumps(url_patterns, ensure_ascii=False)
            mock_json = json.dumps(mock_data, ensure_ascii=False)
            js = f"""
            (() => {{
                const patterns = {patterns_json};
                const mock = {mock_json};
                if (!window.__openmimi_orig_fetch) {{
                    window.__openmimi_orig_fetch = window.fetch;
                }}
                window.fetch = function(resource, init) {{
                    const url = (resource instanceof Request) ? resource.url : String(resource);
                    for (const p of patterns) {{
                        if (url.includes(p)) {{
                            const response = new Response(mock.body || '', {{
                                status: mock.status || 200,
                                headers: mock.headers || {{'Content-Type': 'application/json'}},
                            }});
                            return Promise.resolve(response);
                        }}
                    }}
                    return window.__openmimi_orig_fetch(resource, init);
                }};
                return {{ok: true, patterns, mock}};
            }})()
            """
            try:
                result = await self._exec("eval", js, "--json")
                data = self._parse_data(result.stdout)
                result_value = data.get("result") if isinstance(data, dict) else None
                return ToolResult(
                    output=f"Mock responses set: {json.dumps(result_value, ensure_ascii=False)[:500]}",
                    details={"mock_patterns": url_patterns, "mock_data": mock_data},
                )
            except Exception as exc:
                return ToolResult(output=f"mock_response failed: {exc}", is_error=True)

        if modify_action == "clear":
            js = """
            (() => {
                if (window.__openmimi_orig_fetch) {
                    window.fetch = window.__openmimi_orig_fetch;
                    window.__openmimi_orig_fetch = null;
                }
                if (window.__openmimi_orig_xhr_open) {
                    window.XMLHttpRequest.prototype.open = window.__openmimi_orig_xhr_open;
                    window.XMLHttpRequest.prototype.send = window.__openmimi_orig_xhr_send;
                    window.__openmimi_orig_xhr_open = null;
                    window.__openmimi_orig_xhr_send = null;
                }
                return {ok: true};
            })()
            """
            try:
                result = await self._exec("eval", js, "--json")
                return ToolResult(output="Network modifications cleared")
            except Exception as exc:
                return ToolResult(output=f"clear failed: {exc}", is_error=True)

        return ToolResult(output=f"Unknown network_modify action: {modify_action}", is_error=True)

    async def _do_storage(self, inp: dict[str, Any]) -> ToolResult:
        """Read or modify browser storage: localStorage, sessionStorage, or cookies."""
        storage_action = inp.get("storage_action", "get")
        storage_type = inp.get("storage_type", "localStorage")
        key = inp.get("storage_key", "")
        value = inp.get("storage_value", "")

        if storage_type == "cookies":
            if storage_action == "get":
                js = "(() => ({cookies: document.cookie}))()"
            elif storage_action == "set":
                if not key:
                    return ToolResult(output="cookie set requires 'storage_key' (name=value)", is_error=True)
                js = f"(() => {{ document.cookie = {json.dumps(key)}; return {{ok: true}}; }})()"
            elif storage_action == "clear":
                js = """
                (() => {
                    const cookies = document.cookie.split(';');
                    for (let c of cookies) {
                        const [name] = c.trim().split('=');
                        document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
                    }
                    return {ok: true};
                })()
                """
            else:
                return ToolResult(output=f"Unsupported cookie action: {storage_action}", is_error=True)
        else:
            # localStorage or sessionStorage
            store = "localStorage" if storage_type == "localStorage" else "sessionStorage"
            if storage_action == "get":
                if key:
                    js = f"(() => ({{value: {store}.getItem({json.dumps(key)})}}))()"
                else:
                    js = f"(() => {{ const items = {{}}; for (let i = 0; i < {store}.length; i++) {{ const k = {store}.key(i); items[k] = {store}.getItem(k); }} return items; }})()"
            elif storage_action == "set":
                if not key:
                    return ToolResult(output="storage set requires 'storage_key'", is_error=True)
                js = f"(() => {{ {store}.setItem({json.dumps(key)}, {json.dumps(value)}); return {{ok: true}}; }})()"
            elif storage_action == "delete":
                if not key:
                    return ToolResult(output="storage delete requires 'storage_key'", is_error=True)
                js = f"(() => {{ {store}.removeItem({json.dumps(key)}); return {{ok: true}}; }})()"
            elif storage_action == "clear":
                js = f"(() => {{ {store}.clear(); return {{ok: true}}; }})()"
            elif storage_action == "list":
                js = f"(() => {{ const keys = []; for (let i = 0; i < {store}.length; i++) {{ keys.push({store}.key(i)); }} return {{keys}}; }})()"
            else:
                return ToolResult(output=f"Unsupported storage action: {storage_action}", is_error=True)

        try:
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            return ToolResult(
                output=json.dumps(result_value, ensure_ascii=False, indent=2)[:4000],
                details={"storage_type": storage_type, "action": storage_action, "key": key},
            )
        except Exception as exc:
            return ToolResult(output=f"storage failed: {exc}", is_error=True)

    async def _do_pdf(self, inp: dict[str, Any]) -> ToolResult:
        """Save the current page as a PDF via CDP printToPDF."""
        file_path = inp.get("file_path")
        if not file_path:
            return ToolResult(output="pdf requires 'file_path'", is_error=True)
        js = f"""
        (async () => {{
            try {{
                const result = await window.__openmimi_cdp_send('Page.printToPDF', {{}});
                if (result && result.data) {{
                    const binary = atob(result.data);
                    const bytes = new Uint8Array(binary.length);
                    for (let i = 0; i < binary.length; i++) {{
                        bytes[i] = binary.charCodeAt(i);
                    }}
                    const blob = new Blob([bytes], {{type: 'application/pdf'}});
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = {json.dumps(os.path.basename(file_path))};
                    a.click();
                    URL.revokeObjectURL(url);
                    return {{ok: true, path: {json.dumps(file_path)}}};
                }}
                return {{error: 'CDP printToPDF failed', result}};
            }} catch (e) {{
                return {{error: e.message}};
            }}
        }})()
        """
        # Fallback: use window.print() approach
        fallback_js = f"""
        (() => {{
            try {{
                window.print();
                return {{ok: true, note: "Triggered browser print dialog. Save as PDF manually.", path: {json.dumps(file_path)}}};
            }} catch (e) {{
                return {{error: e.message}};
            }}
        }})()
        """
        try:
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            if isinstance(result_value, dict) and result_value.get("ok"):
                return ToolResult(
                    output=f"PDF saved to {file_path}",
                    details={"path": file_path},
                )
            # Try fallback
            result = await self._exec("eval", fallback_js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            return ToolResult(
                output=json.dumps(result_value, ensure_ascii=False, indent=2)[:4000],
                details={"path": file_path},
            )
        except Exception as exc:
            return ToolResult(output=f"pdf failed: {exc}", is_error=True)

    async def _do_console(self, inp: dict[str, Any]) -> ToolResult:
        """Capture recent browser console logs."""
        level = inp.get("console_level", "all")
        setup_js = """
        (() => {
            if (window.__openmimi_console_logs) return {ok: true, already: true};
            window.__openmimi_console_logs = [];
            const origLog = console.log;
            const origError = console.error;
            const origWarn = console.warn;
            const origInfo = console.info;
            function capture(level, args) {
                const msg = args.map(a => {
                    try { return JSON.stringify(a); }
                    catch (e) { return String(a); }
                }).join(' ');
                window.__openmimi_console_logs.push({level, message: msg.substring(0, 500), time: Date.now()});
                if (window.__openmimi_console_logs.length > 200) {
                    window.__openmimi_console_logs.shift();
                }
            }
            console.log = function(...args) { capture('log', args); origLog.apply(console, args); };
            console.error = function(...args) { capture('error', args); origError.apply(console, args); };
            console.warn = function(...args) { capture('warn', args); origWarn.apply(console, args); };
            console.info = function(...args) { capture('info', args); origInfo.apply(console, args); };
            return {ok: true, already: false};
        })()
        """
        js = f"""
        (() => {{
            let logs = window.__openmimi_console_logs || [];
            const level = {json.dumps(level)};
            if (level !== 'all') {{
                logs = logs.filter(l => l.level === level);
            }}
            return {{
                count: logs.length,
                logs: logs.slice(-30)
            }};
        }})()
        """
        try:
            await self._exec("eval", setup_js, "--json")
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            return ToolResult(
                output=json.dumps(result_value, ensure_ascii=False, indent=2)[:4000],
                details={"level": level},
            )
        except Exception as exc:
            return ToolResult(output=f"console failed: {exc}", is_error=True)

    async def _do_clear_cache(self, _inp: dict[str, Any]) -> ToolResult:
        """Clear cookies, localStorage, sessionStorage, and reload."""
        js = """
        (() => {
            // Clear localStorage
            localStorage.clear();
            // Clear sessionStorage
            sessionStorage.clear();
            // Clear cookies
            const cookies = document.cookie.split(';');
            for (let c of cookies) {
                const [name] = c.trim().split('=');
                document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
            }
            return {ok: true};
        })()
        """
        try:
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            output = json.dumps(result_value, ensure_ascii=False, indent=2)[:2000]
            return ToolResult(output=f"Cache cleared. {output}")
        except Exception as exc:
            return ToolResult(output=f"clear_cache failed: {exc}", is_error=True)

    async def _do_set_viewport(self, inp: dict[str, Any]) -> ToolResult:
        """Resize the browser viewport to the specified width and height."""
        width = inp.get("width")
        height = inp.get("height")
        if width is None or height is None:
            return ToolResult(output="set_viewport requires 'width' and 'height'", is_error=True)
        js = f"""
        (() => {{
            window.resizeTo({int(width)}, {int(height)});
            return {{width: window.innerWidth, height: window.innerHeight, screenWidth: window.screen.width, screenHeight: window.screen.height}};
        }})()
        """
        try:
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            output = json.dumps(result_value, ensure_ascii=False, indent=2)[:2000]
            return ToolResult(output=f"Viewport set. {output}")
        except Exception as exc:
            return ToolResult(output=f"set_viewport failed: {exc}", is_error=True)

    async def _do_save_session(self, inp: dict[str, Any]) -> ToolResult:
        """Save cookies, localStorage, and sessionStorage to a JSON file."""
        file_path = inp.get("file_path")
        if not file_path:
            return ToolResult(output="save_session requires 'file_path'", is_error=True)
        js = """
        (async () => {
            const local = {};
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                local[k] = localStorage.getItem(k);
            }
            const session = {};
            for (let i = 0; i < sessionStorage.length; i++) {
                const k = sessionStorage.key(i);
                session[k] = sessionStorage.getItem(k);
            }
            return {
                url: window.location.href,
                cookies: document.cookie,
                localStorage: local,
                sessionStorage: session,
            };
        })()
        """
        try:
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(result_value, f, ensure_ascii=False, indent=2)
            return ToolResult(output=f"Session saved to {file_path}")
        except Exception as exc:
            return ToolResult(output=f"save_session failed: {exc}", is_error=True)

    async def _do_load_session(self, inp: dict[str, Any]) -> ToolResult:
        """Restore cookies, localStorage, and sessionStorage from a JSON file."""
        file_path = inp.get("file_path")
        if not file_path:
            return ToolResult(output="load_session requires 'file_path'", is_error=True)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                session_data = json.load(f)
        except Exception as exc:
            return ToolResult(output=f"Failed to read session file: {exc}", is_error=True)

        cookies = session_data.get("cookies", "")
        local = session_data.get("localStorage", {})
        session = session_data.get("sessionStorage", {})

        js_parts = ["(() => {"]
        if cookies:
            for c in cookies.split(";"):
                c = c.strip()
                if c:
                    js_parts.append(f"    document.cookie = {json.dumps(c)};")
        for k, v in local.items():
            js_parts.append(f"    localStorage.setItem({json.dumps(k)}, {json.dumps(v)});")
        for k, v in session.items():
            js_parts.append(f"    sessionStorage.setItem({json.dumps(k)}, {json.dumps(v)});")
        js_parts.append("    return {ok: true};")
        js_parts.append("})()")
        js = "\n".join(js_parts)

        try:
            result = await self._exec("eval", js, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            output = json.dumps(result_value, ensure_ascii=False, indent=2)[:2000]
            return ToolResult(output=f"Session loaded from {file_path}. {output}")
        except Exception as exc:
            return ToolResult(output=f"load_session failed: {exc}", is_error=True)

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
            if self._stealth:
                await self._inject_stealth()
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
        if self._stealth:
            await self._inject_stealth()

    async def _inject_stealth(self) -> None:
        """Inject stealth scripts to mask automation indicators."""
        if not self._started:
            return
        try:
            await self._exec("eval", _STEALTH_JS, "--json")
        except Exception:
            pass

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

    async def _exec(self, *args: str, timeout: float | None = None, _retries: int = 2) -> Any:
        """Run agent-browser CLI and return stdout/stderr.

        Uses subprocess.run in a thread-pool executor because
        asyncio.create_subprocess_* hangs indefinitely with the
        agent-browser native binary on Windows.

        Automatically retries transient failures (timeouts, CDP disconnects)
        with exponential backoff.
        """
        import time
        last_err: Exception | None = None
        for attempt in range(_retries + 1):
            cmd_list = [self._executable]
            if not self._headless:
                cmd_list.append("--headed")
            cmd_list.extend(args)
            shell = self._use_shell
            if attempt > 0:
                wait = 0.5 * (2 ** attempt)
                print(f"[agent-browser retry] attempt {attempt + 1}/{_retries + 1} after {wait:.1f}s", file=sys.stderr, flush=True)
                await asyncio.sleep(wait)
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

            try:
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, _run),
                    timeout=tout + 5.0,
                )
                stdout = result.stdout.strip()
                stderr = result.stderr.strip()

                if result.returncode != 0:
                    # Some errors are transient (stale element, CDP disconnect)
                    err_msg = stderr or stdout
                    is_transient = any(
                        kw in err_msg.lower()
                        for kw in ("timeout", "disconnected", "stale", "detached", "not found", " Protocol error")
                    )
                    if is_transient and attempt < _retries:
                        last_err = RuntimeError(f"agent-browser failed (exit {result.returncode}): {err_msg}")
                        continue
                    raise RuntimeError(
                        f"agent-browser failed (exit {result.returncode}): {err_msg}"
                    )

                class _Result:
                    pass
                r = _Result()
                r.stdout = stdout
                r.stderr = stderr
                r.returncode = result.returncode
                print(f"[agent-browser result] rc={result.returncode} stdout_len={len(stdout)} stderr_len={len(stderr)}", file=sys.stderr, flush=True)
                return r
            except (TimeoutError, asyncio.TimeoutError, subprocess.TimeoutExpired) as exc:
                last_err = exc
                if attempt < _retries:
                    continue
                raise RuntimeError(f"agent-browser timed out after {tout}s (attempted {_retries + 1} times)") from exc
            except RuntimeError:
                raise
            except Exception as exc:
                last_err = exc
                if attempt < _retries:
                    continue
                raise
        # Should never reach here, but satisfy type checker
        raise last_err or RuntimeError("agent-browser exec failed")

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
