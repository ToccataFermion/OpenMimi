"""Scroll actions: scroll, human_scroll, scroll_until, scroll_into_view.

``scroll`` is a single-shot direction+amount call. ``human_scroll`` jitters
the amount and pauses across multiple steps so headless automation looks
less robotic. ``scroll_until`` polls for an element/text and gives up after
``timeout_ms``. ``scroll_into_view`` uses JS ``Element.scrollIntoView`` for
fine-grained alignment (block / behavior).
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from typing import TYPE_CHECKING, Any

from ..result import ToolResult
from . import register

if TYPE_CHECKING:
    from ..agent_browser import AgentBrowserTool


@register("scroll")
async def scroll(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    direction = inp.get("direction", "down")
    amount = inp.get("amount", 500)
    await engine._exec("scroll", direction, str(amount), "--json")
    image = await engine._take_screenshot()
    return ToolResult(
        output=f"Scrolled {direction} {amount}px",
        base64_image=image,
    )


@register("human_scroll")
async def human_scroll(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Scroll in multiple small steps with random pauses, simulating human reading."""
    direction = inp.get("direction", "down")
    amount = inp.get("amount", 500)
    steps = inp.get("steps", 0)
    pause_ms = inp.get("pause_ms", 0)

    if steps <= 0:
        steps = max(5, min(12, amount // 80))
    if pause_ms <= 0:
        pause_ms = random.randint(80, 250)

    step_amount = amount // steps
    direction_map = {"down": "down", "up": "up", "left": "left", "right": "right"}
    scroll_dir = direction_map.get(direction, "down")

    for _ in range(steps):
        jittered_amount = int(step_amount * random.uniform(0.7, 1.3))
        jittered_amount = max(10, jittered_amount)
        try:
            await engine._exec("scroll", scroll_dir, str(jittered_amount), "--json")
        except Exception:
            pass
        delay = (pause_ms * random.uniform(0.7, 1.3)) / 1000.0
        await asyncio.sleep(max(0.05, delay))

    image = await engine._take_screenshot()
    return ToolResult(
        output=f"Human-scrolled {direction} ~{amount}px in {steps} steps",
        base64_image=image,
    )


@register("scroll_until")
async def scroll_until(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Scroll the page in steps until an element or text appears."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    text = inp.get("text", "")
    direction = inp.get("direction", "down")
    step_pixels = inp.get("step_pixels", 500)
    timeout_ms = inp.get("timeout_ms", 10000)
    interval_ms = inp.get("interval_ms", 500)
    selector = ref or target_text

    if not selector and not text:
        return ToolResult(
            output="scroll_until requires 'ref', 'target_text', or 'text'",
            is_error=True,
        )

    direction_map = {"down": "down", "up": "up", "left": "left", "right": "right"}
    scroll_dir = direction_map.get(direction, "down")
    start = time.monotonic()
    steps = 0

    while (time.monotonic() - start) * 1000 < timeout_ms:
        try:
            if selector:
                result = await engine._exec("get", "box", selector, "--json")
                data = engine._parse_data(result.stdout)
                box = data.get("box") if isinstance(data, dict) else None
                if box:
                    return ToolResult(
                        output=f"Found after scrolling {steps} steps: {selector}",
                        details={"box": box, "selector": selector, "steps": steps},
                    )
            if text:
                snapshot = await engine._exec("snapshot", "--json")
                snap_text, _ = engine._parse_snapshot(snapshot.stdout)
                if text in snap_text:
                    return ToolResult(
                        output=f"Found text after scrolling {steps} steps: {text}",
                        details={"text": text, "steps": steps},
                    )
        except Exception:
            pass

        try:
            await engine._exec("scroll", scroll_dir, str(step_pixels), "--json")
            steps += 1
        except Exception:
            pass
        await asyncio.sleep(interval_ms / 1000.0)

    return ToolResult(
        output=f"scroll_until timed out after {timeout_ms}ms ({steps} steps): {selector or text}",
        is_error=True,
    )


@register("scroll_into_view")
async def scroll_into_view(
    engine: "AgentBrowserTool", inp: dict[str, Any]
) -> ToolResult:
    """Scroll an element into view using JS scrollIntoView."""
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    behavior = inp.get("behavior", "smooth")
    block = inp.get("block", "center")
    if not ref and not target_text:
        return ToolResult(
            output="scroll_into_view requires 'ref' or 'target_text'",
            is_error=True,
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
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("error"):
            return ToolResult(
                output=f"scroll_into_view failed: {result_value['error']}",
                is_error=True,
            )
        image = await engine._take_screenshot()
        return ToolResult(
            output=f"Scrolled into view: {json.dumps(result_value, ensure_ascii=False)[:200]}",
            base64_image=image,
        )
    except Exception as exc:
        return ToolResult(
            output=f"scroll_into_view error: {exc}", is_error=True
        )
