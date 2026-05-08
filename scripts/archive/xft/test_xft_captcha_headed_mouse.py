"""Try to solve xft slider CAPTCHA using headed browser + CDP mouse actions."""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def bezier_points(start: tuple[int, int], end: tuple[int, int], steps: int = 30):
    """Generate points along a bezier curve with random jitter."""
    sx, sy = start
    ex, ey = end
    # Control point with some randomness
    cx = (sx + ex) // 2 + random.randint(-30, 30)
    cy = (sy + ey) // 2 + random.randint(-10, 10)

    points = []
    for i in range(steps + 1):
        t = i / steps
        # Quadratic bezier
        x = int((1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * ex)
        y = int((1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * ey)
        # Add micro-jitter (human tremor)
        x += random.randint(-1, 1)
        y += random.randint(-1, 1)
        points.append((x, y))
    return points


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser_args = [
        "--disable-blink-features=AutomationControlled",
    ]
    tool = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=browser_args,
    )

    try:
        log("Step 1: Navigate to xft")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(2.0)

        log("Step 2: Click login tab")
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Step 3: Fill credentials via eval")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const inputs = Array.from(document.querySelectorAll('input.ant-input'));
                    const phone = inputs.find(el => el.type === 'text');
                    const pass = inputs.find(el => el.type === 'password');
                    const checkbox = document.querySelector('input.ant-checkbox-input');
                    function setReactValue(element, value) {
                        const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        valueSetter.call(element, value);
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    if (phone) setReactValue(phone, '18584828398');
                    if (pass) setReactValue(pass, 'Liszt123');
                    if (checkbox && !checkbox.checked) checkbox.click();
                    return {ok: true, hasPhone: !!phone, hasPass: !!pass};
                })()
            """,
        })
        log(f"  Fill result: {result.output}")
        await asyncio.sleep(0.5)

        log("Step 4: Click login button via eval (mousedown/mouseup/click)")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                    if (btn) {
                        const rect = btn.getBoundingClientRect();
                        btn.focus();
                        ['mousedown', 'mouseup', 'click'].forEach(type => {
                            btn.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                        });
                        return {clicked: true, centerX: rect.left + rect.width/2, centerY: rect.top + rect.height/2};
                    }
                    return {clicked: false};
                })()
            """,
        })
        log(f"  Login click result: {result.output}")
        await asyncio.sleep(4.0)

        log("Step 5: Check for CAPTCHA")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const bg = document.querySelector('.bottomImage');
                    function rectInfo(el) {
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return {left: r.left, top: r.top, width: r.width, height: r.height};
                    }
                    return {
                        hasButton: !!btn,
                        hasDrag: !!drag,
                        hasBg: !!bg,
                        btnRect: rectInfo(btn),
                        dragRect: rectInfo(drag),
                        bgRect: rectInfo(bg),
                    };
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"  CAPTCHA check: {json.dumps(data, indent=2)[:500]}")

        if not data.get("hasButton"):
            log("  No CAPTCHA detected - checking login state")
            result = await tool({"action": "snapshot"})
            log(f"  Page text: {result.output[:500] if result.output else 'empty'}")
            return

        btn_rect = data.get("btnRect")
        drag_rect = data.get("dragRect")
        bg_rect = data.get("bgRect")
        if not btn_rect or not drag_rect or not bg_rect:
            log("  Missing rect data, aborting")
            return

        # Calculate positions
        start_x = int(btn_rect["left"] + btn_rect["width"] / 2)
        start_y = int(btn_rect["top"] + btn_rect["height"] / 2)
        # Target: drag the button so dragImage aligns with gap
        # button left at ~180px corresponds to dragImage left at ~167px
        # We need to find the gap. For now, try dragging ~200px.
        # bg width is 340, drag width is 78, so max drag is 340-78 = 262px
        # Try a moderate distance first.
        distance = 200
        end_x = start_x + distance
        end_y = start_y

        log(f"Step 6: Drag slider from ({start_x}, {start_y}) to ({end_x}, {end_y}) using mouse actions")

        # Move to start position
        await tool({"action": "mouse", "mouse_action": "move", "x": start_x, "y": start_y})
        await asyncio.sleep(0.2)

        # Mouse down
        await tool({"action": "mouse", "mouse_action": "down", "button": "left"})
        await asyncio.sleep(0.1)

        # Generate human-like trajectory
        points = bezier_points((start_x, start_y), (end_x, end_y), steps=random.randint(25, 40))
        for i, (x, y) in enumerate(points[1:], 1):
            await tool({"action": "mouse", "mouse_action": "move", "x": x, "y": y})
            # Non-uniform timing
            if i < len(points) - 5:
                await asyncio.sleep(random.uniform(0.01, 0.03))
            else:
                # Slow down near end
                await asyncio.sleep(random.uniform(0.05, 0.15))

        # Micro-adjustment
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await tool({"action": "mouse", "mouse_action": "move", "x": end_x + random.randint(-2, 2), "y": end_y + random.randint(-1, 1)})
        await asyncio.sleep(0.1)

        # Mouse up
        await tool({"action": "mouse", "mouse_action": "up", "button": "left"})
        await asyncio.sleep(2.0)

        log("Step 7: Check result")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Has slider: {'滑块' in text}")
        log(f"  Has puzzle: {'拼图' in text}")
        log(f"  Has fail: {'验证失败' in text}")
        log(f"  Has success: {'验证成功' in text}")
        log(f"  Has workbench: {'工作台' in text}")
        log(f"  Has password login: {'密码登录' in text}")

        log("\nKeeping browser open for 10s...")
        await asyncio.sleep(10.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
