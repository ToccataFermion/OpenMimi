"""Extract / page-state actions.

Family covers:
    snapshot, page_source, get_url, get_title,
    get_attribute, set_attribute, get_property,
    extract, get_box, is_visible, visual_locate

Most handlers are straight ``eval`` shells: build a JS expression, send it
through the daemon, parse ``data.result``. ``snapshot`` is the odd one out
because it also routes through ``engine._detect_captcha`` and tags
``ErrorCode.CAPTCHA_DETECTED`` so downstream code can prompt the LLM to
solve a CAPTCHA visually instead of treating it as an error.
"""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from ..errors import ErrorCode
from ..result import ToolResult
from ..agent_browser import _extract_box
from . import register

if TYPE_CHECKING:
    from ..agent_browser import AgentBrowserTool


@register("snapshot")
async def snapshot(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    snap = await engine._exec("snapshot", "--json")
    text, refs = engine._parse_snapshot(snap.stdout)
    image = await engine._take_screenshot()
    details = {
        "open_tabs": engine._tabs,
        "active_tab": engine._active_tab_index,
        "refs": refs,
    }
    captcha_info = await engine._detect_captcha(text)
    if captcha_info:
        details["captcha_detected"] = True
        details["captcha_type"] = captcha_info["type"]
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


@register("page_source")
async def page_source(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Return the raw HTML source of the current page."""
    include_html = inp.get("include_html", True)
    js = "(() => ({title: document.title, url: window.location.href, html: document.documentElement.outerHTML}))()"
    try:
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result_value, dict):
            return ToolResult(
                output="page_source failed: unexpected response", is_error=True
            )
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


@register("get_url")
async def get_url(
    engine: "AgentBrowserTool", _inp: dict[str, Any]
) -> ToolResult:
    """Return the current page URL."""
    try:
        result = await engine._exec(
            "eval", "(() => window.location.href)()", "--json"
        )
        data = engine._parse_data(result.stdout)
        url = data.get("result") if isinstance(data, dict) else ""
        return ToolResult(output=url, details={"url": url})
    except Exception as exc:
        return ToolResult(output=f"get_url error: {exc}", is_error=True)


@register("get_title")
async def get_title(
    engine: "AgentBrowserTool", _inp: dict[str, Any]
) -> ToolResult:
    """Return the current page title."""
    try:
        result = await engine._exec(
            "eval", "(() => document.title)()", "--json"
        )
        data = engine._parse_data(result.stdout)
        title = data.get("result") if isinstance(data, dict) else ""
        return ToolResult(output=title, details={"title": title})
    except Exception as exc:
        return ToolResult(output=f"get_title error: {exc}", is_error=True)


@register("get_attribute")
async def get_attribute(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Get a DOM attribute of an element by ref or target_text."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    attr_name = str(inp.get("attribute_name", ""))
    if not attr_name:
        return ToolResult(
            output="get_attribute requires 'attribute_name'", is_error=True
        )
    selector = ref or target_text
    if not selector:
        return ToolResult(
            output="get_attribute requires 'ref' or 'target_text'", is_error=True
        )
    if ref and ref.startswith("@"):
        el_expr = "document.querySelector(" + json.dumps(ref.lstrip("@")) + ")"
    elif not target_text:
        el_expr = "document.querySelector(" + json.dumps(selector) + ")"
    else:
        el_expr = (
            "Array.from(document.querySelectorAll('*')).find(e => e.textContent.trim().includes("
            + json.dumps(target_text)
            + "))"
        )
    js = (
        "(() => {\n"
        "  const el = " + el_expr + ";\n"
        "  if (!el) return {error: 'Element not found'};\n"
        "  return {value: el.getAttribute(" + json.dumps(attr_name) + ")};\n"
        "})()"
    )
    try:
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("error"):
            return ToolResult(
                output=f"get_attribute: {result_value['error']}", is_error=True
            )
        value = result_value.get("value") if isinstance(result_value, dict) else None
        return ToolResult(
            output=f"Attribute '{attr_name}' = {value!r}",
            details={"attribute_name": attr_name, "value": value},
        )
    except Exception as exc:
        return ToolResult(output=f"get_attribute error: {exc}", is_error=True)


@register("set_attribute")
async def set_attribute(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Set a DOM attribute of an element by ref or target_text."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    attr_name = str(inp.get("attribute_name", ""))
    attr_value = str(inp.get("attribute_value", ""))
    if not attr_name:
        return ToolResult(
            output="set_attribute requires 'attribute_name'", is_error=True
        )
    selector = ref or target_text
    if not selector:
        return ToolResult(
            output="set_attribute requires 'ref' or 'target_text'", is_error=True
        )
    if ref and ref.startswith("@"):
        el_expr = "document.querySelector(" + json.dumps(ref.lstrip("@")) + ")"
    elif not target_text:
        el_expr = "document.querySelector(" + json.dumps(selector) + ")"
    else:
        el_expr = (
            "Array.from(document.querySelectorAll('*')).find(e => e.textContent.trim().includes("
            + json.dumps(target_text)
            + "))"
        )
    js = (
        "(() => {\n"
        "  const el = " + el_expr + ";\n"
        "  if (!el) return {error: 'Element not found'};\n"
        "  el.setAttribute(" + json.dumps(attr_name) + ", " + json.dumps(attr_value) + ");\n"
        "  return {ok: true};\n"
        "})()"
    )
    try:
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("error"):
            return ToolResult(
                output=f"set_attribute: {result_value['error']}", is_error=True
            )
        image = await engine._take_screenshot()
        return ToolResult(
            output=f"Set attribute '{attr_name}' to {attr_value!r}",
            base64_image=image,
        )
    except Exception as exc:
        return ToolResult(output=f"set_attribute error: {exc}", is_error=True)


@register("get_property")
async def get_property(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Get a JS property of an element by ref or target_text."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    prop_name = str(inp.get("property_name", ""))
    if not prop_name:
        return ToolResult(
            output="get_property requires 'property_name'", is_error=True
        )
    selector = ref or target_text
    if not selector:
        return ToolResult(
            output="get_property requires 'ref' or 'target_text'", is_error=True
        )
    if ref and ref.startswith("@"):
        el_expr = "document.querySelector(" + json.dumps(ref.lstrip("@")) + ")"
    elif not target_text:
        el_expr = "document.querySelector(" + json.dumps(selector) + ")"
    else:
        el_expr = (
            "Array.from(document.querySelectorAll('*')).find(e => e.textContent.trim().includes("
            + json.dumps(target_text)
            + "))"
        )
    js = (
        "(() => {\n"
        "  const el = " + el_expr + ";\n"
        "  if (!el) return {error: 'Element not found'};\n"
        "  return {value: el[" + json.dumps(prop_name) + "]};\n"
        "})()"
    )
    try:
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("error"):
            return ToolResult(
                output=f"get_property: {result_value['error']}", is_error=True
            )
        value = result_value.get("value") if isinstance(result_value, dict) else None
        return ToolResult(
            output=f"Property '{prop_name}' = {value!r}",
            details={"property_name": prop_name, "value": value},
        )
    except Exception as exc:
        return ToolResult(output=f"get_property error: {exc}", is_error=True)


_EXTRACT_INSTRUCTIONS = (
    "get text",
    "headings",
    "links",
    "forms",
    "tables",
    "metadata",
    "images",
)
_EXTRACT_INSTRUCTION_ALIASES = {
    "text": "get text",
    "get_text": "get text",
    "gettext": "get text",
}


@register("extract")
async def extract(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Extract structured data from the page based on instruction."""
    instruction = inp.get("instruction", "get text")
    instruction = _EXTRACT_INSTRUCTION_ALIASES.get(instruction, instruction)

    if instruction == "get text":
        result = await engine._exec(
            "eval", "document.body.innerText", "--json"
        )
        data = engine._parse_data(result.stdout)
        text = data.get("result", "")
        return ToolResult(
            output=text[:4000],
            structured={"instruction": "get text", "data": text},
        )

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
        return ToolResult(
            output=(
                f"Unknown extract instruction {instruction!r}. "
                f"Valid options: {', '.join(_EXTRACT_INSTRUCTIONS)}. "
                "For arbitrary JS use action='eval' instead."
            ),
            is_error=True,
        )

    try:
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        output = json.dumps(result_value, ensure_ascii=False, indent=2)
        return ToolResult(
            output=output[:4000],
            details={"instruction": instruction},
            structured={"instruction": instruction, "data": result_value},
        )
    except Exception as exc:
        return ToolResult(output=f"extract failed: {exc}", is_error=True)


@register("get_box")
async def get_box(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Return the bounding box of an element for OS-level mouse coordination."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    selector = ref or target_text
    if not selector:
        return ToolResult(
            output="get_box requires 'ref' or 'target_text'", is_error=True
        )
    try:
        result = await engine._exec("get", "box", selector, "--json")
        data = engine._parse_data(result.stdout)
        box = _extract_box(data)
        if not box:
            return ToolResult(
                output=f"Could not get box for {selector}", is_error=True
            )
        return ToolResult(
            output=json.dumps(box, ensure_ascii=False, indent=2),
            details={"box": box, "selector": selector},
            structured={"box": box, "selector": selector},
        )
    except Exception as exc:
        return ToolResult(
            output=f"get_box failed for {selector}: {exc}", is_error=True
        )


@register("is_visible")
async def is_visible(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Check if an element is present and visible in the viewport."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    selector = ref or target_text
    if not selector:
        return ToolResult(
            output="is_visible requires 'ref' or 'target_text'", is_error=True
        )
    try:
        if ref:
            css_selector = ref.lstrip("@")
            js = f"""
            (() => {{
                const el = document.querySelector({json.dumps(css_selector)});
                if (!el) return {{visible: false, reason: 'not found'}};
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const visible = style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                return {{visible, tag: el.tagName, rect: {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}}}};
            }})()
            """
        else:
            js = f"""
            (() => {{
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
                let el;
                while (el = walker.nextNode()) {{
                    if ((el.innerText || el.textContent || '').trim().includes({json.dumps(target_text)})) {{
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        const visible = style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                        return {{visible, tag: el.tagName, rect: {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}}}};
                    }}
                }}
                return {{visible: false, reason: 'not found'}};
            }})()
            """
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict):
            visible = result_value.get("visible", False)
            return ToolResult(
                output=f"Visible: {visible}",
                details=result_value,
                structured=result_value,
            )
        return ToolResult(
            output="is_visible returned unexpected format", is_error=True
        )
    except Exception as exc:
        return ToolResult(output=f"is_visible failed: {exc}", is_error=True)


@register("visual_locate")
async def visual_locate(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Find an element on the page by visual template matching using OpenCV."""
    template_path = str(inp.get("template_path", ""))
    confidence = float(inp.get("confidence", 0.8))
    should_click = inp.get("click", False)
    if not template_path:
        return ToolResult(
            output="visual_locate requires 'template_path'", is_error=True
        )
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        return ToolResult(
            output=f"visual_locate requires opencv-python: {exc}", is_error=True
        )

    try:
        old_scale = engine._screenshot_scale
        engine._screenshot_scale = 1.0
        try:
            png_bytes = await engine._take_screenshot_raw()
        finally:
            engine._screenshot_scale = old_scale
        if png_bytes is None:
            return ToolResult(output="Failed to capture screenshot", is_error=True)
        screen = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if screen is None:
            return ToolResult(output="Failed to decode screenshot", is_error=True)
        if template is None:
            return ToolResult(
                output=f"Failed to load template: {template_path}", is_error=True
            )

        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        if max_val >= confidence:
            h, w = template.shape[:2]
            cx = max_loc[0] + w // 2
            cy = max_loc[1] + h // 2
            if should_click:
                await engine._exec("mouse", "move", str(cx), str(cy), "--json")
                await asyncio.sleep(0.05)
                await engine._exec("mouse", "down", "left", "--json")
                await asyncio.sleep(0.05)
                await engine._exec("mouse", "up", "left", "--json")
                await asyncio.sleep(0.1)
            image = await engine._take_screenshot()
            return ToolResult(
                output=f"Found template at ({cx}, {cy}) with confidence {max_val:.3f}",
                base64_image=image,
                details={
                    "x": cx,
                    "y": cy,
                    "confidence": max_val,
                    "width": w,
                    "height": h,
                    "clicked": should_click,
                },
            )
        image = await engine._take_screenshot()
        return ToolResult(
            output=f"Template not found (best confidence: {max_val:.3f}, threshold: {confidence})",
            is_error=True,
            base64_image=image,
        )
    except Exception as exc:
        return ToolResult(output=f"visual_locate error: {exc}", is_error=True)
