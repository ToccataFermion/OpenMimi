"""Tab and session-state actions.

Family covers:
    tab_list, tab_switch, tab_new, tab_close,
    save_session, load_session,
    clipboard, clear_cache

The four ``tab_*`` handlers all touch ``engine._tabs`` /
``engine._active_tab_index`` and call ``engine._refresh_tabs()`` so the
sidecar's tab list and the in-memory mirror stay in sync.

``save_session`` prefers CDP ``Network.getAllCookies`` (captures
HTTP-only cookies) and falls back to ``document.cookie``.
``load_session`` accepts either CDP-shaped cookie arrays
(``{name, value, domain, path}``) or the legacy semicolon-joined
``document.cookie`` string. Both forms restore localStorage and
sessionStorage too.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..result import ToolResult
from . import register

if TYPE_CHECKING:
    from ..agent_browser import AgentBrowserTool


@register("clipboard")
async def clipboard(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    cb_action = inp.get("clipboard_action", "read")
    if cb_action == "read":
        result = await engine._exec("clipboard", "read", "--json")
        data = engine._parse_data(result.stdout)
        text = data.get("text", "")
        return ToolResult(output=f"Clipboard: {text}")
    elif cb_action == "write":
        text = str(inp.get("clipboard_text", ""))
        await engine._exec("clipboard", "write", text, "--json")
        return ToolResult(output=f"Wrote {len(text)} chars to clipboard")
    elif cb_action == "copy":
        await engine._exec("clipboard", "copy", "--json")
        return ToolResult(output="Copied current selection to clipboard")
    elif cb_action == "paste":
        await engine._exec("clipboard", "paste", "--json")
        return ToolResult(output="Pasted clipboard content")
    else:
        return ToolResult(output=f"Unknown clipboard action: {cb_action}", is_error=True)


@register("tab_list")
async def tab_list(
    engine: "AgentBrowserTool", _inp: dict[str, Any]
) -> ToolResult:
    await engine._refresh_tabs()
    lines = [f"Tab {i+1}: {t.get('url', '')}" for i, t in enumerate(engine._tabs)]
    return ToolResult(
        output=f"Active tab: {engine._active_tab_index}\n" + "\n".join(lines),
        details={"open_tabs": engine._tabs, "active_tab": engine._active_tab_index},
    )


@register("tab_switch")
async def tab_switch(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    idx = inp.get("tab_index", 1)
    await engine._refresh_tabs()
    if 1 <= idx <= len(engine._tabs):
        tab_id = engine._tabs[idx - 1].get("id", f"t{idx}")
        await engine._exec("tab", tab_id, "--json")
        engine._active_tab_index = idx
        image = await engine._take_screenshot()
        return ToolResult(
            output=f"Switched to tab {idx}",
            base64_image=image,
            details={"open_tabs": engine._tabs, "active_tab": idx},
        )
    return ToolResult(output=f"Invalid tab index {idx}")


@register("tab_new")
async def tab_new(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    url = inp.get("url", "about:blank")
    await engine._exec("tab", "new", url, "--json")
    await engine._refresh_tabs()
    image = await engine._take_screenshot()
    return ToolResult(
        output=f"New tab opened: {url}",
        base64_image=image,
        details={"open_tabs": engine._tabs, "active_tab": engine._active_tab_index},
    )


@register("tab_close")
async def tab_close(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    idx = inp.get("tab_index")
    await engine._refresh_tabs()
    if idx and 1 <= idx <= len(engine._tabs):
        tab_id = engine._tabs[idx - 1].get("id", f"t{idx}")
        await engine._exec("tab", "close", tab_id, "--json")
        await engine._refresh_tabs()
        return ToolResult(output=f"Closed tab {idx}")
    return ToolResult(output="tab_close requires valid tab_index")


@register("clear_cache")
async def clear_cache(
    engine: "AgentBrowserTool", _inp: dict[str, Any]
) -> ToolResult:
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
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        output = json.dumps(result_value, ensure_ascii=False, indent=2)[:2000]
        return ToolResult(output=f"Cache cleared. {output}")
    except Exception as exc:
        return ToolResult(output=f"clear_cache failed: {exc}", is_error=True)


@register("save_session")
async def save_session(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Save cookies, localStorage, and sessionStorage to a JSON file.

    Cookies are captured via CDP Network.getAllCookies when available so
    that HTTP-only cookies are included. Falls back to document.cookie."""
    file_path = inp.get("file_path")
    if not file_path:
        return ToolResult(output="save_session requires 'file_path'", is_error=True)

    cdp_js = """
    (async () => {
        try {
            const result = await window.__openmimi_cdp_send('Network.getAllCookies');
            return {ok: true, cookies: result.cookies, method: 'cdp'};
        } catch (e) {
            return {error: e.message, method: 'cdp_failed'};
        }
    })()
    """
    cookies: Any = []
    cookie_method = "js"
    try:
        result = await engine._exec("eval", cdp_js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("ok"):
            cookies = result_value.get("cookies", [])
            cookie_method = "cdp"
    except Exception:
        pass

    if not cookies:
        try:
            result = await engine._exec("eval", "(() => ({cookies: document.cookie}))()", "--json")
            data = engine._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            cookies = result_value.get("cookies", "") if isinstance(result_value, dict) else ""
        except Exception:
            cookies = ""

    storage_js = """
    (() => {
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
        return {url: window.location.href, localStorage: local, sessionStorage: session};
    })()
    """
    try:
        result = await engine._exec("eval", storage_js, "--json")
        data = engine._parse_data(result.stdout)
        storage_value = data.get("result") if isinstance(data, dict) else {}
    except Exception:
        storage_value = {}

    session_data = {
        "url": storage_value.get("url", ""),
        "cookies": cookies,
        "localStorage": storage_value.get("localStorage", {}),
        "sessionStorage": storage_value.get("sessionStorage", {}),
    }

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        return ToolResult(
            output=f"Session saved to {file_path} (cookies via {cookie_method})",
            details={"cookie_method": cookie_method, "cookie_count": len(cookies) if isinstance(cookies, list) else 0},
        )
    except Exception as exc:
        return ToolResult(output=f"save_session failed: {exc}", is_error=True)


@register("load_session")
async def load_session(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Restore cookies, localStorage, and sessionStorage from a JSON file.

    Supports both CDP cookie arrays (with domain/path) and legacy
    semicolon-separated cookie strings."""
    file_path = inp.get("file_path")
    if not file_path:
        return ToolResult(output="load_session requires 'file_path'", is_error=True)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            session_data = json.load(f)
    except Exception as exc:
        return ToolResult(output=f"Failed to read session file: {exc}", is_error=True)

    cookies = session_data.get("cookies", [])
    local = session_data.get("localStorage", {})
    session = session_data.get("sessionStorage", {})

    if isinstance(cookies, list) and cookies:
        js_parts = ["(async () => {"]
        for c in cookies:
            if not isinstance(c, dict):
                continue
            name = c.get("name", "")
            if not name:
                continue
            value = c.get("value", "")
            domain = c.get("domain", "")
            path = c.get("path", "/")
            if domain:
                js_parts.append(
                    f"    try {{ await window.__openmimi_cdp_send('Network.setCookie', {json.dumps({'name': name, 'value': value, 'domain': domain, 'path': path})}); }} catch (e) {{}}"
                )
            else:
                js_parts.append(
                    f"    try {{ await window.__openmimi_cdp_send('Network.setCookie', {{ name: {json.dumps(name)}, value: {json.dumps(value)}, url: window.location.href }}); }} catch (e) {{}}"
                )
        for k, v in local.items():
            js_parts.append(f"    localStorage.setItem({json.dumps(k)}, {json.dumps(v)});")
        for k, v in session.items():
            js_parts.append(f"    sessionStorage.setItem({json.dumps(k)}, {json.dumps(v)});")
        js_parts.append("    return {ok: true, method: 'cdp'};")
        js_parts.append("})()")
        js = "\n".join(js_parts)
    elif isinstance(cookies, str) and cookies:
        js_parts = ["(() => {"]
        for c in cookies.split(";"):
            c = c.strip()
            if c:
                js_parts.append(f"    document.cookie = {json.dumps(c)};")
        for k, v in local.items():
            js_parts.append(f"    localStorage.setItem({json.dumps(k)}, {json.dumps(v)});")
        for k, v in session.items():
            js_parts.append(f"    sessionStorage.setItem({json.dumps(k)}, {json.dumps(v)});")
        js_parts.append("    return {ok: true, method: 'js'};")
        js_parts.append("})()")
        js = "\n".join(js_parts)
    else:
        js_parts = ["(() => {"]
        for k, v in local.items():
            js_parts.append(f"    localStorage.setItem({json.dumps(k)}, {json.dumps(v)});")
        for k, v in session.items():
            js_parts.append(f"    sessionStorage.setItem({json.dumps(k)}, {json.dumps(v)});")
        js_parts.append("    return {ok: true, method: 'storage_only'};")
        js_parts.append("})()")
        js = "\n".join(js_parts)

    try:
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        output = json.dumps(result_value, ensure_ascii=False, indent=2)[:2000]
        return ToolResult(output=f"Session loaded from {file_path}. {output}")
    except Exception as exc:
        return ToolResult(output=f"load_session failed: {exc}", is_error=True)
