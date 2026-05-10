"""Network, CDP, storage, pdf, console, and screenshot actions.

Family covers:
    cdp, screenshot,
    network_log, network_modify,
    storage,
    pdf, console

``cdp`` is the raw CDP escape hatch — JS calls
``window.__openmimi_cdp_send(method, params)`` and unwraps the result.
``network_log`` installs a per-page fetch/XHR interceptor that records
URL/method/body/response and returns the latest 20 entries; ``network_modify``
covers four sub-actions (user_agent, inject_headers, block_urls, mock_response,
clear) that patch fetch and XHR similarly.

``storage`` handles localStorage / sessionStorage / cookies, with CDP-first
cookie support (``Network.getAllCookies`` etc.) falling back to
``document.cookie``. The ``_try_cdp_then_fallback`` helper exists only to
serve cookie sub-actions.

``pdf`` saves the current page via CDP ``Page.printToPDF``, falling back to
the browser print dialog when CDP is unavailable. ``console`` installs a
console-method shim that captures the last 200 logs and returns up to 30
filtered by level. ``screenshot`` honours ``OPENMIMI_ENABLE_SCREENSHOTS``;
when disabled the call returns text explaining how to opt in.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any

from ...utils.env_flags import screenshots_disabled
from ..result import ToolResult
from . import register

if TYPE_CHECKING:
    from ..agent_browser import AgentBrowserTool


@register("cdp")
async def cdp(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Send an arbitrary CDP command via window.__openmimi_cdp_send."""
    cdp_method = inp.get("cdp_method", "")
    cdp_params = inp.get("cdp_params", {})
    if not cdp_method:
        return ToolResult(output="cdp requires 'cdp_method' (e.g. 'Runtime.evaluate')", is_error=True)
    params_json = json.dumps(cdp_params, ensure_ascii=False)
    js = f"""
    (async () => {{
        try {{
            const result = await window.__openmimi_cdp_send({json.dumps(cdp_method)}, {params_json});
            return {{ok: true, result}};
        }} catch (e) {{
            return {{error: e.message}};
        }}
    }})()
    """
    try:
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("error"):
            return ToolResult(
                output=f"CDP error: {result_value['error']}", is_error=True
            )
        return ToolResult(
            output=json.dumps(result_value, ensure_ascii=False, indent=2)[:4000],
            details={"cdp_method": cdp_method, "cdp_params": cdp_params},
        )
    except Exception as exc:
        return ToolResult(output=f"cdp failed: {exc}", is_error=True)


@register("screenshot")
async def screenshot(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    if screenshots_disabled():
        return ToolResult(
            output="Screenshots disabled by default. Set OPENMIMI_ENABLE_SCREENSHOTS=1 or pass --screenshots to enable.",
            base64_image=None,
        )
    path = inp.get("path")
    annotate = inp.get("annotate", False)
    image = await engine._take_screenshot(path_override=path, annotate=annotate)
    label = "Annotated screenshot" if annotate else "Screenshot"
    return ToolResult(output=f"{label} taken", base64_image=image)


@register("network_log")
async def network_log(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Inject JS to intercept network requests and responses, then return captured traffic."""
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
                    (r.body && r.body.includes(filter)) ||
                    (r.responseBody && r.responseBody.includes(filter))
                );
            }}
            return {{
                count: filtered.length,
                requests: filtered.slice(-20)
            }};
        }})()
    """
    setup_js = """
        (() => {
            if (window.__openmimi_network_hooked) return {ok: true, already: true};
            window.__openmimi_captured_requests = [];
            const _maxBody = 1000;
            const _store = (entry) => {
                window.__openmimi_captured_requests.push(entry);
                if (window.__openmimi_captured_requests.length > 200) {
                    window.__openmimi_captured_requests.shift();
                }
            };
            const origFetch = window.fetch;
            window.fetch = function(...args) {
                const url = args[0] instanceof Request ? args[0].url : String(args[0]);
                const options = args[1] || {};
                const entry = {
                    type: 'fetch',
                    url: url,
                    method: options.method || 'GET',
                    body: options.body ? String(options.body).substring(0, 500) : null,
                    time: Date.now(),
                };
                const p = origFetch.apply(this, args);
                p.then(r => {
                    entry.status = r.status;
                    entry.statusText = r.statusText;
                    const clone = r.clone();
                    clone.text().then(t => { entry.responseBody = t.substring(0, _maxBody); }).catch(() => {});
                }).catch(e => { entry.error = e.message; });
                _store(entry);
                return p;
            };
            const origXHR = window.XMLHttpRequest.prototype.open;
            const origXHRSend = window.XMLHttpRequest.prototype.send;
            window.XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                this._om_method = method;
                this._om_url = url;
                return origXHR.call(this, method, url, ...rest);
            };
            window.XMLHttpRequest.prototype.send = function(body) {
                const entry = {
                    type: 'xhr',
                    url: this._om_url,
                    method: this._om_method || 'GET',
                    body: body ? String(body).substring(0, 500) : null,
                    time: Date.now(),
                };
                const self = this;
                const onLoad = () => {
                    entry.status = self.status;
                    entry.statusText = self.statusText;
                    entry.responseBody = (self.responseText || "").substring(0, _maxBody);
                };
                const onError = () => { entry.error = "XHR failed"; };
                this.addEventListener("load", onLoad);
                this.addEventListener("error", onError);
                _store(entry);
                return origXHRSend.call(this, body);
            };
            window.__openmimi_network_hooked = true;
            return {ok: true, already: false};
        })()
    """
    try:
        await engine._exec("eval", setup_js, "--json")
        if duration_ms > 0:
            await asyncio.sleep(duration_ms / 1000.0)
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        requests = data.get("requests", []) if isinstance(data, dict) else []
        return ToolResult(
            output=json.dumps(data, ensure_ascii=False, indent=2)[:4000],
            details={"requests": requests},
        )
    except Exception as exc:
        return ToolResult(
            output=f"network_log failed: {exc}", is_error=True
        )


@register("network_modify")
async def network_modify(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Modify network behavior via JS patching or CDP: inject headers, block URLs,
    mock responses, or set User-Agent."""
    modify_action = inp.get("modify_action", "inject_headers")

    if modify_action == "user_agent":
        ua = inp.get("user_agent", "")
        if not ua:
            return ToolResult(output="network_modify user_agent requires 'user_agent'", is_error=True)
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
            result = await engine._exec("eval", cdp_js, "--json")
            data = engine._parse_data(result.stdout)
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
            result = await engine._exec("eval", js, "--json")
            data = engine._parse_data(result.stdout)
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
            result = await engine._exec("eval", js, "--json")
            data = engine._parse_data(result.stdout)
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
                        this.status = mock.status || 200;
                        this.statusText = 'OK';
                        this.responseText = mock.body || '';
                        this.readyState = 4;
                        const self = this;
                        setTimeout(() => {{
                            self.dispatchEvent(new Event('load'));
                            self.dispatchEvent(new Event('loadend'));
                        }}, 0);
                        return;
                    }}
                }}
                return window.__openmimi_orig_xhr_send.call(this, body);
            }};
            return {{ok: true, patterns, mock}};
        }})()
        """
        try:
            result = await engine._exec("eval", js, "--json")
            data = engine._parse_data(result.stdout)
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
            await engine._exec("eval", js, "--json")
            return ToolResult(output="Network modifications cleared")
        except Exception as exc:
            return ToolResult(output=f"clear failed: {exc}", is_error=True)

    return ToolResult(output=f"Unknown network_modify action: {modify_action}", is_error=True)


async def _try_cdp_then_fallback(
    engine: "AgentBrowserTool",
    cdp_js: str,
    fallback_js: str,
    storage_type: str,
    storage_action: str,
    key: str,
) -> ToolResult:
    """Try CDP cookie API first, fall back to JS document.cookie on failure."""
    try:
        result = await engine._exec("eval", cdp_js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("ok"):
            return ToolResult(
                output=json.dumps(result_value, ensure_ascii=False, indent=2)[:4000],
                details={"storage_type": storage_type, "action": storage_action, "key": key, "method": "cdp"},
            )
    except Exception:
        pass
    try:
        result = await engine._exec("eval", fallback_js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        return ToolResult(
            output=json.dumps(result_value, ensure_ascii=False, indent=2)[:4000],
            details={"storage_type": storage_type, "action": storage_action, "key": key, "method": "js_fallback"},
        )
    except Exception as exc:
        return ToolResult(output=f"storage failed: {exc}", is_error=True)


@register("storage")
async def storage(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Read or modify browser storage: localStorage, sessionStorage, or cookies."""
    storage_action = inp.get("storage_action", "get")
    storage_type = inp.get("storage_type", "localStorage")
    key = inp.get("storage_key", "")
    value = inp.get("storage_value", "")

    if storage_type == "cookies":
        if storage_action == "get":
            cdp_js = """
            (async () => {
                try {
                    const result = await window.__openmimi_cdp_send('Network.getAllCookies');
                    return {ok: true, method: 'cdp', cookies: result.cookies};
                } catch (e) {
                    return {error: e.message, method: 'cdp_failed'};
                }
            })()
            """
            fallback_js = "(() => ({cookies: document.cookie}))()"
            return await _try_cdp_then_fallback(engine, cdp_js, fallback_js, "cookies", storage_action, key)

        if storage_action == "set":
            if not key:
                return ToolResult(output="cookie set requires 'storage_key' (name=value)", is_error=True)
            cdp_js = f"""
            (async () => {{
                try {{
                    const key = {json.dumps(key)};
                    const idx = key.indexOf('=');
                    const name = idx >= 0 ? key.substring(0, idx).trim() : key.trim();
                    const val = idx >= 0 ? key.substring(idx + 1).trim() : '';
                    await window.__openmimi_cdp_send('Network.setCookie', {{
                        name: name,
                        value: val,
                        url: window.location.href,
                    }});
                    return {{ok: true, method: 'cdp'}};
                }} catch (e) {{
                    return {{error: e.message, method: 'cdp_failed'}};
                }}
            }})()
            """
            fallback_js = f"(() => {{ document.cookie = {json.dumps(key)}; return {{ok: true}}; }})()"
            return await _try_cdp_then_fallback(engine, cdp_js, fallback_js, "cookies", storage_action, key)

        if storage_action == "delete":
            if not key:
                return ToolResult(output="cookie delete requires 'storage_key' (cookie name)", is_error=True)
            cdp_js = f"""
            (async () => {{
                try {{
                    const name = {json.dumps(key)};
                    await window.__openmimi_cdp_send('Network.deleteCookies', {{
                        name: name,
                        url: window.location.href,
                    }});
                    return {{ok: true, method: 'cdp', deleted: name}};
                }} catch (e) {{
                    return {{error: e.message, method: 'cdp_failed'}};
                }}
            }})()
            """
            fallback_js = f"""
            (() => {{
                const name = {json.dumps(key)};
                document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
                return {{ok: true, method: 'js_fallback', deleted: name}};
            }})()
            """
            return await _try_cdp_then_fallback(engine, cdp_js, fallback_js, "cookies", storage_action, key)

        if storage_action == "clear":
            cdp_js = """
            (async () => {
                try {
                    await window.__openmimi_cdp_send('Network.clearBrowserCookies');
                    return {ok: true, method: 'cdp'};
                } catch (e) {
                    return {error: e.message, method: 'cdp_failed'};
                }
            })()
            """
            fallback_js = """
            (() => {
                const cookies = document.cookie.split(';');
                for (let c of cookies) {
                    const [name] = c.trim().split('=');
                    document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
                }
                return {ok: true};
            })()
            """
            return await _try_cdp_then_fallback(engine, cdp_js, fallback_js, "cookies", storage_action, key)

        return ToolResult(output=f"Unsupported cookie action: {storage_action}", is_error=True)

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
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        return ToolResult(
            output=json.dumps(result_value, ensure_ascii=False, indent=2)[:4000],
            details={"storage_type": storage_type, "action": storage_action, "key": key},
        )
    except Exception as exc:
        return ToolResult(output=f"storage failed: {exc}", is_error=True)


@register("pdf")
async def pdf(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
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
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("ok"):
            return ToolResult(
                output=f"PDF saved to {file_path}",
                details={"path": file_path},
            )
        result = await engine._exec("eval", fallback_js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        return ToolResult(
            output=json.dumps(result_value, ensure_ascii=False, indent=2)[:4000],
            details={"path": file_path},
        )
    except Exception as exc:
        return ToolResult(output=f"pdf failed: {exc}", is_error=True)


@register("console")
async def console(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
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
        await engine._exec("eval", setup_js, "--json")
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        return ToolResult(
            output=json.dumps(result_value, ensure_ascii=False, indent=2)[:4000],
            details={"level": level},
        )
    except Exception as exc:
        return ToolResult(output=f"console failed: {exc}", is_error=True)
