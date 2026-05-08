"""Test xft login with anti-detection browser args + image-based gap detection + CDP drag."""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image

from openmimi.tools.agent_browser import AgentBrowserTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def find_gap_position(screenshot_path: str, bottom_rect: dict, drag_rect: dict) -> int | None:
    """Find gap x-position in viewport coordinates."""
    img = Image.open(screenshot_path)
    rgb = img.convert("RGB")
    w, h = img.size
    scale_x = w / 1280
    scale_y = h / 800

    bx = int(bottom_rect["x"] * scale_x)
    by = int(bottom_rect["y"] * scale_y)
    bw = int(bottom_rect["w"] * scale_x)
    bh = int(bottom_rect["h"] * scale_y)

    if bx < 0 or by < 0 or bx + bw > w or by + bh > h:
        return None

    crop = rgb.crop((bx, by, bx + bw, by + bh))
    cw, ch = crop.size
    piece_w = int(drag_rect["w"] * scale_x)

    # Simple edge detection: find column with highest brightness variance
    from PIL import ImageFilter
    edges = crop.convert("L").filter(ImageFilter.FIND_EDGES)
    edge_pixels = edges.load()

    best_x = None
    best_score = 0
    for x in range(piece_w + 5, cw - piece_w):
        score = sum(edge_pixels[x, y] for y in range(ch))
        if score > best_score:
            best_score = score
            best_x = x

    if best_x is None:
        return None

    gap_x_viewport = int(best_x / scale_x)
    return gap_x_viewport


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    # Anti-detection args
    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-web-security",
        "--disable-features=IsolateOrigins,site-per-process",
    ]
    tool = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        browser_args=browser_args,
    )

    try:
        log("Step 1: Navigate")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})

        log("Step 2: Click login")
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Step 3: Fill credentials")
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
                    return {ok: true};
                })()
            """,
        })
        log(f"  Fill: {result.output}")
        await asyncio.sleep(0.5)

        log("Step 4: Click login to trigger CAPTCHA")
        await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                    if (btn) {
                        btn.focus();
                        ['mousedown', 'mouseup', 'click'].forEach(type => {
                            btn.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                        });
                    }
                    return {clicked: !!btn};
                })()
            """,
        })
        await asyncio.sleep(2.0)

        log("Step 5: Get CAPTCHA positions")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const bottom = document.querySelector('.bottomImage');
                    const drag = document.querySelector('.dragImage');
                    const br = bottom ? bottom.getBoundingClientRect() : null;
                    const dr = drag ? drag.getBoundingClientRect() : null;
                    return {
                        bottom: br ? {x: br.x, y: br.y, w: br.width, h: br.height} : null,
                        drag: dr ? {x: dr.x, y: dr.y, w: dr.width, h: dr.height} : null
                    };
                })()
            """,
        })
        rects = json.loads(result.output or "{}")
        log(f"  Rects: {rects}")

        bottom_rect = rects.get("bottom")
        drag_rect = rects.get("drag")
        if not bottom_rect or not drag_rect:
            log("  Could not find CAPTCHA images")
            return

        log("Step 6: Take screenshot")
        screenshot_path = os.path.join(download_dir, "captcha.png")
        await tool({"action": "screenshot", "path": screenshot_path})
        log(f"  Screenshot: {screenshot_path}")

        log("Step 7: Analyze gap position")
        gap_x = find_gap_position(screenshot_path, bottom_rect, drag_rect)
        if gap_x is None:
            log("  Could not find gap, using fallback")
            gap_x = drag_rect["x"] + 200

        drag_center = drag_rect["x"] + drag_rect["w"] / 2
        distance = int(gap_x - drag_center)
        log(f"  Gap x={gap_x}, drag center={drag_center}, distance={distance}")

        log("Step 8: Perform drag via agent-browser mouse commands")
        start_x = int(drag_center)
        start_y = int(drag_rect["y"] + drag_rect["h"] / 2)
        end_x = start_x + distance
        end_y = start_y

        log(f"  Drag from ({start_x}, {start_y}) to ({end_x}, {end_y})")
        await tool._exec("mouse", "move", str(start_x), str(start_y), "--json")
        await asyncio.sleep(0.2)
        await tool._exec("mouse", "down", "--json")
        await asyncio.sleep(0.2)

        # Human-like drag with easing and jitter
        steps = 25
        for i in range(1, steps + 1):
            t = i / steps
            ease = 1 - (1 - t) ** 3
            cx = start_x + int(distance * ease)
            cy = end_y + (i % 3 - 1)  # small y jitter
            await tool._exec("mouse", "move", str(cx), str(cy), "--json")
            await asyncio.sleep(0.03 + (i % 3) * 0.01)

        await tool._exec("mouse", "up", "--json")
        await asyncio.sleep(0.2)
        log("  Drag complete")

        await asyncio.sleep(3.0)

        log("Step 9: Check result")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Snapshot len: {len(text)}")
        log(f"  Contains '滑块': {'滑块' in text}")
        log(f"  Contains '拼图': {'拼图' in text}")
        log(f"  Contains '工作台': {'工作台' in text}")
        log(f"  Contains '首页': {'首页' in text}")
        log(f"  Contains '密码登录': {'密码登录' in text}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
