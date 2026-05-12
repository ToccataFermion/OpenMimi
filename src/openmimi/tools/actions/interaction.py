"""Interaction actions: clicks, typing, hover, key presses, key combos.

Family covers:
    click, right_click, double_click, check, uncheck,
    type, fill, react_fill, press, key_combo, hover

Most handlers come in two flavours: a ``ref`` (e.g. ``@e3``) or a
``target_text`` to find by text. Mouse-driven clicks (right_click,
double_click, force-click fallback) compute element box via
``get box`` and then drive CDP ``mouse`` directly so React SPAs and
overlay-protected elements still react.
"""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from ..result import ToolResult
from ..agent_browser import _extract_box
from . import register
from ._keys import cdp_key_code

if TYPE_CHECKING:
    from ..agent_browser import AgentBrowserTool


@register("click")
async def click(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    force = inp.get("force", False)
    selector = ref or target_text
    if not selector:
        return ToolResult(output="click requires 'ref' or 'target_text'")

    if force:
        return await _click_with_mouse(engine, selector)

    if ref:
        result = await engine._exec("click", ref, "--json")
    else:
        result = await engine._exec(
            "find", "text", target_text, "click", "--json"
        )
    data = engine._parse_data(result.stdout)
    await engine._switch_to_newest_tab()
    image = await engine._take_screenshot()
    clicked = data.get("clicked", "element")
    details = {
        "open_tabs": engine._tabs,
        "active_tab": engine._active_tab_index,
    }
    return ToolResult(
        output=f"Clicked {clicked}",
        base64_image=image,
        details=details,
    )


async def _click_with_mouse(
    engine: "AgentBrowserTool", selector: str
) -> ToolResult:
    """Fallback click using CDP mouse move/down/up.

    Bypasses synthetic-click limitations on React SPAs and overlay-
    protected pages that ignore standard automation clicks.
    """
    try:
        result = await engine._exec("get", "box", selector, "--json")
        data = engine._parse_data(result.stdout)
        box = _extract_box(data)
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

    await engine._exec("mouse", "move", str(x), str(y), "--json")
    await asyncio.sleep(0.05)
    await engine._exec("mouse", "down", "left", "--json")
    await asyncio.sleep(0.05)
    await engine._exec("mouse", "up", "left", "--json")
    await asyncio.sleep(0.1)

    image = await engine._take_screenshot()
    return ToolResult(
        output=f"Force-clicked {selector} at ({x}, {y}) via mouse down/up",
        base64_image=image,
    )


@register("right_click")
async def right_click(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Right-click an element via CDP mouse sequence."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    selector = ref or target_text
    if not selector:
        return ToolResult(output="right_click requires 'ref' or 'target_text'")
    try:
        result = await engine._exec("get", "box", selector, "--json")
        data = engine._parse_data(result.stdout)
        box = _extract_box(data)
        if not box:
            return ToolResult(
                output=f"right_click failed: could not get box for {selector}",
                is_error=True,
            )
        x = int(box.get("x", 0) + box.get("width", 0) / 2)
        y = int(box.get("y", 0) + box.get("height", 0) / 2)
    except Exception as exc:
        return ToolResult(
            output=f"right_click failed getting box: {exc}",
            is_error=True,
        )
    await engine._exec("mouse", "move", str(x), str(y), "--json")
    await asyncio.sleep(0.05)
    await engine._exec("mouse", "down", "right", "--json")
    await asyncio.sleep(0.05)
    await engine._exec("mouse", "up", "right", "--json")
    await asyncio.sleep(0.1)
    image = await engine._take_screenshot()
    return ToolResult(
        output=f"Right-clicked {selector} at ({x}, {y})",
        base64_image=image,
    )


@register("double_click")
async def double_click(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Double-click an element via CDP mouse sequence."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    selector = ref or target_text
    if not selector:
        return ToolResult(output="double_click requires 'ref' or 'target_text'")
    try:
        result = await engine._exec("get", "box", selector, "--json")
        data = engine._parse_data(result.stdout)
        box = _extract_box(data)
        if not box:
            return ToolResult(
                output=f"double_click failed: could not get box for {selector}",
                is_error=True,
            )
        x = int(box.get("x", 0) + box.get("width", 0) / 2)
        y = int(box.get("y", 0) + box.get("height", 0) / 2)
    except Exception as exc:
        return ToolResult(
            output=f"double_click failed getting box: {exc}",
            is_error=True,
        )
    await engine._exec("mouse", "move", str(x), str(y), "--json")
    for _ in range(2):
        await asyncio.sleep(0.05)
        await engine._exec("mouse", "down", "left", "--json")
        await asyncio.sleep(0.05)
        await engine._exec("mouse", "up", "left", "--json")
    await asyncio.sleep(0.1)
    image = await engine._take_screenshot()
    return ToolResult(
        output=f"Double-clicked {selector} at ({x}, {y})",
        base64_image=image,
    )


@register("check")
async def check(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    if ref:
        await engine._exec("check", ref, "--json")
    elif target_text:
        await engine._exec("find", "text", target_text, "check", "--json")
    else:
        return ToolResult(output="check requires 'ref' or 'target_text'")
    image = await engine._take_screenshot()
    return ToolResult(output="Checked element", base64_image=image)


@register("uncheck")
async def uncheck(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    if ref:
        await engine._exec("uncheck", ref, "--json")
    elif target_text:
        await engine._exec("find", "text", target_text, "uncheck", "--json")
    else:
        return ToolResult(output="uncheck requires 'ref' or 'target_text'")
    image = await engine._take_screenshot()
    return ToolResult(output="Unchecked element", base64_image=image)


@register("type")
async def type_(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    value = inp.get("value", "")
    if ref:
        await engine._exec("type", ref, value, "--json")
    elif target_text:
        # agent-browser's `find <locator> <value> type <text>` reports
        # "Unknown subaction: type" — the chained-action path doesn't
        # accept type at all. Workaround: focus via find+click, then
        # send keystrokes to the focused element.
        await engine._exec("find", "text", target_text, "click", "--json")
        await engine._exec("keyboard", "type", value, "--json")
    else:
        return ToolResult(output="type requires 'ref' or 'target_text'")
    image = await engine._take_screenshot()
    return ToolResult(
        output=f"Typed {len(value)} character(s)",
        base64_image=image,
    )


@register("fill")
async def fill(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    value = inp.get("value", "")
    if ref:
        await engine._exec("fill", ref, value, "--json")
    elif target_text:
        # agent-browser's `find <locator> <value> fill <text>` argv parser
        # drops the trailing <text>: when the element is found, the daemon
        # returns "Missing 'value' for fill subaction" even though value
        # was provided. Repro'd directly on the CLI in cycles 65/81/89.
        # Workaround: focus via find+click, select-all to clear, then
        # send keystrokes — same semantics as fill (clear-then-type).
        await engine._exec("find", "text", target_text, "click", "--json")
        await engine._exec("press", "Control+a", "--json")
        await engine._exec("keyboard", "type", value, "--json")
    else:
        return ToolResult(output="fill requires 'ref' or 'target_text'")
    image = await engine._take_screenshot()
    return ToolResult(
        output=f"Filled with {len(value)} character(s)",
        base64_image=image,
    )


@register("react_fill")
async def react_fill(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Fill an input using React-aware value setting (prototype setter + events)."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    value = str(inp.get("value", ""))
    selector = ref or target_text
    if not selector:
        return ToolResult(
            output="react_fill requires 'ref' or 'target_text'", is_error=True
        )
    if not value:
        return ToolResult(output="react_fill requires 'value'", is_error=True)

    if ref:
        # agent-browser snapshot refs (e.g. "e22") are NOT CSS selectors —
        # they're opaque handles into the latest snapshot. Resolve to a box
        # first, then locate the element via `document.elementFromPoint`
        # so the React-aware setter can run against the real DOM node.
        try:
            box_result = await engine._exec("get", "box", ref, "--json")
            box_data = engine._parse_data(box_result.stdout)
            box = _extract_box(box_data)
            if not box:
                return ToolResult(
                    output=f"react_fill failed: could not resolve ref {ref}",
                    is_error=True,
                )
            cx = box.get("x", 0) + box.get("width", 0) / 2
            cy = box.get("y", 0) + box.get("height", 0) / 2
        except Exception as exc:
            return ToolResult(
                output=f"react_fill failed resolving ref {ref}: {exc}",
                is_error=True,
            )
        js = f"""
        (() => {{
            let el = document.elementFromPoint({cx}, {cy});
            // Walk up to find an <input>/<textarea>/<select> if elementFromPoint
            // lands on a wrapper (common with custom React inputs).
            while (el && !['INPUT','TEXTAREA','SELECT'].includes(el.tagName)) {{
                const inner = el.querySelector('input, textarea, select');
                if (inner) {{ el = inner; break; }}
                el = el.parentElement;
            }}
            if (!el) return {{error: 'element not found at point'}};
            const tag = el.tagName.toLowerCase();
            if (tag === 'input' || tag === 'textarea') {{
                const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value') ||
                                   Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
                if (descriptor && descriptor.set) {{
                    descriptor.set.call(el, {json.dumps(value)});
                }} else {{
                    el.value = {json.dumps(value)};
                }}
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{ok: true, tag: tag, method: 'prototype_setter'}};
            }} else if (tag === 'select') {{
                el.value = {json.dumps(value)};
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{ok: true, tag: tag, method: 'select_value'}};
            }}
            return {{error: 'unsupported tag: ' + tag}};
        }})()
        """
    else:
        js = f"""
        (() => {{
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
            let el;
            while (el = walker.nextNode()) {{
                if ((el.innerText || el.textContent || '').trim().includes({json.dumps(target_text)})) {{
                    const input = el.querySelector('input, textarea, select');
                    if (!input) return {{error: 'no input found inside matched element'}};
                    const tag = input.tagName.toLowerCase();
                    if (tag === 'input' || tag === 'textarea') {{
                        const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value') ||
                                           Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
                        if (descriptor && descriptor.set) {{
                            descriptor.set.call(input, {json.dumps(value)});
                        }} else {{
                            input.value = {json.dumps(value)};
                        }}
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return {{ok: true, tag: tag, method: 'prototype_setter'}};
                    }} else if (tag === 'select') {{
                        input.value = {json.dumps(value)};
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return {{ok: true, tag: tag, method: 'select_value'}};
                    }}
                    return {{error: 'unsupported tag: ' + tag}};
                }}
            }}
            return {{error: 'element not found'}};
        }})()
        """
    try:
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("error"):
            return ToolResult(
                output=f"react_fill failed: {result_value['error']}",
                is_error=True,
            )
        image = await engine._take_screenshot()
        return ToolResult(
            output=f"React-filled {len(value)} character(s) via {result_value.get('method', 'unknown')}",
            base64_image=image,
            details=result_value,
        )
    except Exception as exc:
        return ToolResult(output=f"react_fill error: {exc}", is_error=True)


@register("press")
async def press(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    key = inp.get("key", "Enter")
    await engine._exec("press", key, "--json")
    image = await engine._take_screenshot()
    return ToolResult(output=f"Pressed {key}", base64_image=image)


@register("key_combo")
async def key_combo(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Send a key combination (e.g. ['Control','a']) via CDP dispatchKeyEvent."""
    keys = inp.get("keys", [])
    if not keys or not isinstance(keys, list):
        return ToolResult(
            output="key_combo requires 'keys' array (e.g. ['Control','c'])",
            is_error=True,
        )

    key_objs = [{"key": k, "code": cdp_key_code(k)} for k in keys]

    down_events = "\n".join(
        f"    await window.__openmimi_cdp_send('Input.dispatchKeyEvent', {{type: 'keyDown', key: {json.dumps(k['key'])}, code: {json.dumps(k['code'])}}});"
        for k in key_objs
    )
    up_events = "\n".join(
        f"    await window.__openmimi_cdp_send('Input.dispatchKeyEvent', {{type: 'keyUp', key: {json.dumps(k['key'])}, code: {json.dumps(k['code'])}}});"
        for k in reversed(key_objs)
    )

    js = f"""
    (async () => {{
        try {{
            {down_events}
            {up_events}
            return {{ok: true, keys: {json.dumps(keys)}}};
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
                output=f"key_combo failed: {result_value['error']}",
                is_error=True,
            )
        image = await engine._take_screenshot()
        return ToolResult(
            output=f"Key combo pressed: {'+'.join(keys)}",
            base64_image=image,
            details=result_value,
        )
    except Exception as exc:
        return ToolResult(output=f"key_combo error: {exc}", is_error=True)


@register("hover")
async def hover(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    if ref:
        await engine._exec("hover", ref, "--json")
    elif target_text:
        await engine._exec("find", "text", target_text, "hover", "--json")
    else:
        return ToolResult(output="hover requires 'ref' or 'target_text'")
    image = await engine._take_screenshot()
    return ToolResult(output="Hovered element", base64_image=image)
