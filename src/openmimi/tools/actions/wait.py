"""Wait / synchronization actions.

Family covers:
    wait, wait_for, wait_for_disappear,
    wait_for_navigation, wait_for_network_idle

All five handlers are time-based polling loops. ``wait`` is the only one
that defers to the daemon's own ``wait`` subcommand; the rest drive the
loop in Python and probe via ``eval`` / ``get box`` / ``snapshot`` so the
agent-browser sidecar stays free to handle other tools concurrently.

``wait_for_network_idle`` installs a one-shot fetch/XHR interceptor on
the page (``window.__openmimi_network_idle_*``) so it can count in-flight
requests; the install is idempotent (re-runs cheaply skip).
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from ..result import ToolResult
from ..agent_browser import _extract_box
from . import register

if TYPE_CHECKING:
    from ..agent_browser import AgentBrowserTool


@register("wait")
async def wait(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    ms = inp.get("milliseconds", 1000)
    await engine._exec("wait", str(ms), "--json")
    return ToolResult(output=f"Waited {ms}ms")


@register("wait_for")
async def wait_for(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
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
                result = await engine._exec("get", "box", selector, "--json")
                data = engine._parse_data(result.stdout)
                box = _extract_box(data)
                if box:
                    return ToolResult(
                        output=f"Element found: {selector}",
                        details={"box": box, "selector": selector},
                    )
            if text:
                snapshot = await engine._exec("snapshot", "--json")
                snap_text, _ = engine._parse_snapshot(snapshot.stdout)
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


@register("wait_for_disappear")
async def wait_for_disappear(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Wait for an element or text to disappear from the page."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    text = inp.get("text", "")
    timeout_ms = inp.get("timeout_ms", 10000)
    interval_ms = inp.get("interval_ms", 500)
    selector = ref or target_text

    if not selector and not text:
        return ToolResult(
            output="wait_for_disappear requires 'ref', 'target_text', or 'text'", is_error=True
        )

    start = time.monotonic()
    while (time.monotonic() - start) * 1000 < timeout_ms:
        try:
            found = False
            if selector:
                result = await engine._exec("get", "box", selector, "--json")
                data = engine._parse_data(result.stdout)
                box = _extract_box(data)
                if box:
                    found = True
            if text:
                snapshot = await engine._exec("snapshot", "--json")
                snap_text, _ = engine._parse_snapshot(snapshot.stdout)
                if text in snap_text:
                    found = True
            if not found:
                return ToolResult(
                    output=f"Element/text disappeared: {selector or text}",
                    details={"selector": selector, "text": text},
                )
        except Exception:
            return ToolResult(
                output=f"Element/text disappeared (page error): {selector or text}",
                details={"selector": selector, "text": text},
            )
        await asyncio.sleep(interval_ms / 1000.0)

    return ToolResult(
        output=f"wait_for_disappear timed out after {timeout_ms}ms: {selector or text} is still present",
        is_error=True,
    )


@register("wait_for_navigation")
async def wait_for_navigation(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Wait for the page URL to change, indicating navigation has occurred."""
    expected_url = inp.get("expected_url", "")
    timeout_ms = inp.get("timeout_ms") or inp.get("milliseconds") or 10000
    interval_ms = inp.get("interval_ms", 500)
    try:
        result = await engine._exec("eval", "(() => window.location.href)()", "--json")
        data = engine._parse_data(result.stdout)
        start_url = data.get("result") if isinstance(data, dict) else ""
    except Exception:
        start_url = ""
    start = time.monotonic()
    while (time.monotonic() - start) * 1000 < timeout_ms:
        try:
            result = await engine._exec("eval", "(() => window.location.href)()", "--json")
            data = engine._parse_data(result.stdout)
            current_url = data.get("result") if isinstance(data, dict) else ""
            if current_url != start_url:
                if not expected_url or expected_url in current_url:
                    image = await engine._take_screenshot()
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


@register("wait_for_network_idle")
async def wait_for_network_idle(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Wait until no network requests have been active for a specified duration.

    Useful for SPAs that load data asynchronously after navigation
    or clicks. Tracks in-flight fetch/XHR requests and returns only when
    the count drops to zero and stays there for ``idle_duration_ms``.
    """
    idle_duration_ms = inp.get("idle_duration_ms", 2000)
    timeout_ms = inp.get("timeout_ms", 30000)
    interval_ms = inp.get("interval_ms", 500)

    setup_js = """
    (() => {
        if (window.__openmimi_network_idle_hooked) return {ok: true, already: true};
        window.__openmimi_network_idle_count = 0;
        window.__openmimi_network_last_active = Date.now();

        const origFetch = window.fetch;
        window.fetch = function(...args) {
            window.__openmimi_network_idle_count++;
            window.__openmimi_network_last_active = Date.now();
            return origFetch.apply(this, args).finally(() => {
                window.__openmimi_network_idle_count--;
                window.__openmimi_network_last_active = Date.now();
            });
        };

        const origXHRSend = window.XMLHttpRequest.prototype.send;
        window.XMLHttpRequest.prototype.send = function(body) {
            window.__openmimi_network_idle_count++;
            window.__openmimi_network_last_active = Date.now();
            const self = this;
            const onDone = () => {
                window.__openmimi_network_idle_count--;
                window.__openmimi_network_last_active = Date.now();
                self.removeEventListener('loadend', onDone);
            };
            this.addEventListener('loadend', onDone);
            return origXHRSend.call(this, body);
        };

        window.__openmimi_network_idle_hooked = true;
        return {ok: true, already: false};
    })()
    """
    try:
        await engine._exec("eval", setup_js, "--json")
    except Exception:
        pass

    start = time.monotonic()
    while (time.monotonic() - start) * 1000 < timeout_ms:
        try:
            poll_js = f"""
            (() => {{
                const count = window.__openmimi_network_idle_count || 0;
                const lastActive = window.__openmimi_network_last_active || 0;
                const now = Date.now();
                const idleFor = now - lastActive;
                return {{
                    count,
                    idleFor,
                    idle: count === 0 && idleFor >= {int(idle_duration_ms)}
                }};
            }})()
            """
            result = await engine._exec("eval", poll_js, "--json")
            data = engine._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            if isinstance(result_value, dict) and result_value.get("idle"):
                return ToolResult(
                    output=f"Network idle for {result_value.get('idleFor', 0)}ms (no active requests)",
                    details={
                        "idle_duration_ms": result_value.get("idleFor", 0),
                        "in_flight": result_value.get("count", 0),
                    },
                )
        except Exception:
            pass
        await asyncio.sleep(interval_ms / 1000.0)

    return ToolResult(
        output=f"wait_for_network_idle timed out after {timeout_ms}ms",
        is_error=True,
    )
