"""AgentBrowserTool: wraps the agent-browser Rust CLI for Anthropic-style tool actions.

Design intent: agent-browser is a sidecar process (Rust CLI) that speaks CDP.
We communicate via subprocess, parse --json output, and translate into OpenMimi's
ToolResult format.

Key workflow:
- LLM must first call snapshot to discover @eN refs
- Subsequent actions use refs for precise, stable targeting
- Text-based locators (find text "..." click) are available as fallback
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..utils.env_flags import screenshots_disabled
from .base import ToolBase
from .errors import ErrorCode
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Operate a Chromium browser via agent-browser (Rust CLI). "
    "Core workflow: 1) Call action='snapshot' to get an accessibility tree with @eN refs; "
    "2) Use those refs in action='click' / 'right_click' / 'double_click' / 'type' / 'fill' / 'react_fill' / 'hover' / 'drag' via the 'ref' field; "
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
    "Get URL: action='get_url' returns the current page URL. "
    "Get title: action='get_title' returns the current page title. "
    "Get attribute: action='get_attribute' with 'ref' or 'target_text' and 'attribute_name' reads a DOM attribute (e.g. href, src, data-id). "
    "Set attribute: action='set_attribute' with 'ref' or 'target_text', 'attribute_name', and 'attribute_value' writes a DOM attribute. "
    "Get property: action='get_property' with 'ref' or 'target_text' and 'property_name' reads a JS property (e.g. value, checked, innerText). "
    "Wait for navigation: action='wait_for_navigation' waits for the URL to change after a click or form submission. "
    "Wait for network idle: action='wait_for_network_idle' waits until no fetch/XHR requests are active for idle_duration_ms (default 2000). "
    "Element coordinates: action='get_box' with 'ref' or 'target_text' returns the "
    "element's bounding box (x, y, width, height) for OS-level mouse coordination. "
    "Visibility check: action='is_visible' with 'ref' or 'target_text' returns whether "
    "the element is present and visible (not display:none, visibility:hidden, or zero size). "
    "Visual locate: action='visual_locate' with 'template_path' uses OpenCV template "
    "matching on the page screenshot to find elements by visual appearance. Optional "
    "'click'=true to click on the matched region. Useful for canvas UIs, custom icons, "
    "or when DOM selectors are unreliable.\n"
    "Human-like scroll: action='human_scroll' performs scroll in multiple small steps with "
    "random pauses between each step, simulating human reading behavior and reducing bot detection.\n"
    "Scroll until found: action='scroll_until' scrolls in steps until an element (ref/target_text) or text appears. "
    "Useful for infinite scroll and long forms. Parameters: direction, step_pixels, timeout_ms, interval_ms.\n"
    "Dynamic content: action='wait_for' with 'ref', 'target_text', or 'text' waits until "
    "the element or text appears on the page (useful for React/Vue SPAs that render lazily). "
    "wait_for_disappear: action='wait_for_disappear' with 'ref', 'target_text', or 'text' waits until "
    "the element or text is no longer present (useful for loading spinners, CAPTCHA modals, overlays). "
    "Network debugging: action='network_log' with optional 'duration_ms' and 'filter' "
    "intercepts fetch/XHR requests and captures response status codes and bodies to discover hidden API endpoints. "
    "Network modification: action='network_modify' with 'modify_action' can inject headers, "
    "block URLs by pattern, mock responses (fetch + XHR), or override User-Agent. Use this to bypass "
    "anti-bot detection or inject auth tokens into API requests. "
    "React form fill: action='react_fill' with 'ref'/'target_text' and 'value' uses the "
    "HTMLInputElement.prototype.value setter + dispatchEvent(input/change) pattern, which "
    "is required for React/Vue controlled inputs that ignore direct value assignment. "
    "Use react_fill instead of fill on React SPAs.\n"
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
    "Timezone: action='set_timezone' with 'timezone' (e.g. 'Asia/Shanghai') overrides browser timezone via CDP. Pass empty string to reset.\n"
    "Locale: action='set_locale' with 'locale' (e.g. 'zh-CN') overrides browser locale via CDP. Pass empty string to reset.\n"
    "Geolocation: action='set_geolocation' with 'latitude', 'longitude', and optional 'accuracy' overrides GPS location via CDP. Omit coords to clear.\n"
    "CDP raw access: action='cdp' with 'cdp_method' and optional 'cdp_params' sends arbitrary "
    "Chrome DevTools Protocol commands via window.__openmimi_cdp_send. Use as an escape hatch "
    "for CDP features not covered by other actions.\n"
    "Session persistence: action='save_session' with 'file_path' persists cookies/storage; "
    "action='load_session' with 'file_path' restores them to avoid repeated logins. "
    "Persistent profile: pass user_data_dir when creating the tool to reuse cookies, cache, "
    "and extensions across sessions (more robust than save_session/load_session). "
    "Proxy: pass proxy='http://host:port' when creating the tool to route traffic through a proxy. "
    "Slow motion: pass slow_mo_ms when creating the tool to add a randomized delay after each action, "
    "making automation less detectable by bot detection systems (e.g. slow_mo_ms=200).\n"
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
        screenshot_scale: float = 1.0,
        slow_mo_ms: int = 0,
    ) -> None:
        self._download_dir = Path(download_dir)
        self._screenshot_scale = max(0.1, min(1.0, float(screenshot_scale)))
        self._slow_mo_ms = max(0, int(slow_mo_ms))
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
                            "right_click",
                            "double_click",
                            "check",
                            "uncheck",
                            "type",
                            "fill",
                            "react_fill",
                            "press",
                            "key_combo",
                            "hover",
                            "scroll",
                            "human_scroll",
                            "scroll_until",
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
                            "is_visible",
                            "visual_locate",
                            "wait_for",
                            "wait_for_disappear",
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
                            "get_url",
                            "get_title",
                            "get_attribute",
                            "set_attribute",
                            "get_property",
                            "wait_for_navigation",
                            "wait_for_network_idle",
                            "emulate_device",
                            "set_timezone",
                            "set_locale",
                            "set_geolocation",
                            "cdp",
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
                    "step_pixels": {
                        "type": "integer",
                        "description": "Scroll step size in pixels for action='scroll_until' (default 500).",
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
                    "template_path": {
                        "type": "string",
                        "description": "Path to a template image (PNG/JPG) for action='visual_locate'.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Minimum confidence threshold for visual_locate (0.0-1.0, default 0.8).",
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
                        "description": "Timeout in milliseconds for wait_for and wait_for_network_idle (default 10000).",
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
                    "attribute_name": {
                        "type": "string",
                        "description": "DOM attribute name for action='get_attribute' or 'set_attribute' (e.g. 'href', 'src', 'data-id').",
                    },
                    "attribute_value": {
                        "type": "string",
                        "description": "Value to set for action='set_attribute'.",
                    },
                    "property_name": {
                        "type": "string",
                        "description": "JS property name for action='get_property' (e.g. 'value', 'checked', 'innerText', 'innerHTML').",
                    },
                    "device_name": {
                        "type": "string",
                        "enum": ["iPhone 14", "iPhone 14 Pro Max", "Pixel 7", "iPad Mini", "reset"],
                        "description": "Device preset for action='emulate_device'. Use 'reset' to restore desktop.",
                    },
                    "idle_duration_ms": {
                        "type": "integer",
                        "description": "For action='wait_for_network_idle': ms of no network activity before returning (default 2000).",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Timezone ID for action='set_timezone' (e.g. 'Asia/Shanghai', 'America/New_York'). Pass empty string to reset.",
                    },
                    "locale": {
                        "type": "string",
                        "description": "Locale for action='set_locale' (e.g. 'zh-CN', 'en-US'). Pass empty string to reset.",
                    },
                    "latitude": {
                        "type": "number",
                        "description": "Latitude for action='set_geolocation' (-90 to 90).",
                    },
                    "longitude": {
                        "type": "number",
                        "description": "Longitude for action='set_geolocation' (-180 to 180).",
                    },
                    "accuracy": {
                        "type": "number",
                        "description": "Accuracy in meters for action='set_geolocation' (default 100).",
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
            result = await self._dispatch(action, tool_input)
            if self._slow_mo_ms > 0 and action not in ("close", "wait", "wait_for", "wait_for_navigation", "wait_for_network_idle", "screenshot"):
                jitter = self._slow_mo_ms * 0.2
                delay = (self._slow_mo_ms + random.uniform(-jitter, jitter)) / 1000.0
                delay = max(0.05, delay)
                await asyncio.sleep(delay)
            return result
        except Exception as e:
            # Capture screenshot on error to aid debugging
            image: str | None = None
            try:
                image = await self._take_screenshot()
            except Exception:
                pass
            err_text = f"Error: {e}"
            if image:
                err_text += "\n[A screenshot of the error state is attached]"
            return ToolResult(output=err_text, is_error=True, base64_image=image)

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
        # Migrated handlers live in tools/actions/<family>.py and self-register
        # at import time via the @register decorator. Consult that registry
        # first so the giant in-class dispatch table can shrink piece by piece.
        from . import actions as _actions

        registered = _actions.get(action)
        if registered is not None:
            return await registered(self, inp)

        return ToolResult(output=f"Unknown action: {action}")

    # ------------------------------------------------------------------ #
    #  Action implementations
    # ------------------------------------------------------------------ #

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
                '.imageVerifyDragButton', '.bottomImage',
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

    def is_warming_up(self) -> bool:
        """Return True while the eager-warmup background thread is running.

        Safe to call from the CLI before the first user task: lets the REPL
        surface the in-flight 5-minute Windows cold-start so the user knows
        why the first tool call may take a while.
        """
        t = self._warmup_thread
        return t is not None and t.is_alive()

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
            # Try CDP pre-injection so stealth runs before any page scripts
            cdp_inject = f"""
            (async () => {{
                try {{
                    await window.__openmimi_cdp_send('Page.addScriptToEvaluateOnNewDocument', {{
                        source: {json.dumps(_STEALTH_JS)}
                    }});
                    return {{ok: true, method: 'cdp_preinject'}};
                }} catch (e) {{
                    return {{error: e.message}};
                }}
            }})()
            """
            result = await self._exec("eval", cdp_inject, "--json")
            data = self._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            if isinstance(result_value, dict) and result_value.get("ok"):
                # CDP pre-injection succeeded; also run immediately for current page
                await self._exec("eval", _STEALTH_JS, "--json")
                return
        except Exception:
            pass
        # Fallback: standard eval injection for current page only
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
        raw = await self._take_screenshot_raw(path_override=path_override, annotate=annotate)
        if raw is None:
            return None
        return base64.b64encode(raw).decode("ascii")

    async def _take_screenshot_raw(self, path_override: str | None = None, annotate: bool = False) -> bytes | None:
        if screenshots_disabled():
            return None
        try:
            path = Path(path_override) if path_override else _SCREENSHOT_DIR / f"ab_{int(time.time() * 1000)}.png"
            args = ["screenshot", str(path)]
            if annotate:
                args.append("--annotate")
            args.append("--json")
            result = await self._exec(*args)
            data = self._parse_data(result.stdout)
            returned_path = data.get("path", str(path))
            if Path(returned_path).exists():
                with open(returned_path, "rb") as f:
                    png_bytes = f.read()
                if self._screenshot_scale < 1.0:
                    try:
                        from PIL import Image
                        img = Image.open(io.BytesIO(png_bytes))
                        new_size = (
                            max(1, int(img.width * self._screenshot_scale)),
                            max(1, int(img.height * self._screenshot_scale)),
                        )
                        img = img.resize(new_size, Image.Resampling.LANCZOS)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG", optimize=True)
                        png_bytes = buf.getvalue()
                    except Exception:
                        pass
                return png_bytes
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
