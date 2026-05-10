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
from .agent_browser_schema import TOOL_DESCRIPTION as _TOOL_DESCRIPTION
from .agent_browser_schema import build_input_schema as _build_input_schema
from .base import ToolBase
from .errors import ErrorCode
from .result import ToolResult

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
            "input_schema": _build_input_schema(),
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
