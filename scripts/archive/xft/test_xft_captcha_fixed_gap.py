"""Test xft login with fixed gap detection + improved human-like drag."""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image, ImageFilter

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

    # Edge detection
    edges = crop.convert("L").filter(ImageFilter.FIND_EDGES)
    edge_pixels = edges.load()

    # Also use color variance - gap edges often have strong contrast
    best_x = None
    best_score = 0
    # Search from piece_width+5 to avoid the left edge, and leave room on right
    for x in range(piece_w + 5, cw - piece_w):
        score = sum(edge_pixels[x, y] for y in range(ch))
        if score > best_score:
            best_score = score
            best_x = x

    if best_x is None:
        return None

    # Convert back to viewport coordinates: add crop offset, then unscale
    gap_x_viewport = int((best_x + bx) / scale_x)
    return gap_x_viewport


def find_gap_by_color(screenshot_path: str, bottom_rect: dict, drag_rect: dict) -> int | None:
    """Alternative: find gap by looking for a vertical strip with unusual color patterns."""
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
    pixels = crop.load()
    cw, ch = crop.size
    piece_w = int(drag_rect["w"] * scale_x)

    # For each column, compute average brightness and variance
    best_x = None
    best_score = 0
    for x in range(piece_w + 5, cw - piece_w):
        # Sample a few rows to compute brightness difference from neighbors
        score = 0
        for y in range(0, ch, 5):
            r, g, b = pixels[x, y]
            brightness = (r + g + b) / 3
            # Compare with neighbors
            if x > 0:
                rl, gl, bl = pixels[x - 1, y]
                score += abs(brightness - (rl + gl + bl) / 3)
            if x < cw - 1:
                rr, gr, br_ = pixels[x + 1, y]
                score += abs(brightness - (rr + gr + br_) / 3)
        if score > best_score:
            best_score = score
            best_x = x

    if best_x is None:
        return None

    return int((best_x + bx) / scale_x)


async def do_login(tool: AgentBrowserTool) -> None:
    """Navigate to xft and fill login form to trigger CAPTCHA."""
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


async def perform_drag(tool: AgentBrowserTool, start_x: int, start_y: int, distance: int) -> None:
    """Perform a human-like drag with variable speed and pauses."""
    log(f"  Drag from ({start_x}, {start_y}) by {distance}px")

    await tool._exec("mouse", "move", str(start_x), str(start_y), "--json")
    await asyncio.sleep(0.2)
    await tool._exec("mouse", "down", "--json")
    await asyncio.sleep(0.2)

    # Human-like profile: accelerate, then decelerate, with small pauses
    steps = 40
    positions = []
    for i in range(1, steps + 1):
        t = i / steps
        # Ease-in-out
        if t < 0.5:
            ease = 2 * t * t
        else:
            ease = 1 - pow(-2 * t + 2, 2) / 2
        positions.append(start_x + int(distance * ease))

    # Add a small pause halfway
    pause_step = steps // 2

    for i, cx in enumerate(positions):
        # Small y jitter
        cy = start_y + (i % 5 - 2)
        await tool._exec("mouse", "move", str(cx), str(cy), "--json")

        # Variable delay
        delay = 0.02
        if i < steps * 0.2:
            delay = 0.01  # Fast start
        elif i > steps * 0.8:
            delay = 0.04  # Slow end
        if i == pause_step:
            delay = 0.15  # Pause halfway
        await asyncio.sleep(delay)

    await tool._exec("mouse", "up", "--json")
    await asyncio.sleep(0.3)
    log("  Drag complete")


async def get_captcha_state(tool: AgentBrowserTool) -> dict:
    """Get current CAPTCHA element state."""
    result = await tool({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.imageVerifyDragButton');
                const drag = document.querySelector('.dragImage');
                const text = document.querySelector('.imageVerifyDragText');
                const modal = document.querySelector('.ant-modal-body');
                return {
                    btnLeft: btn ? btn.style.left : null,
                    dragLeft: drag ? drag.style.left : null,
                    text: text ? text.textContent.trim().slice(0, 50) : null,
                    hasModal: !!modal
                };
            })()
        """,
    })
    return json.loads(result.output or "{}")


async def check_result(tool: AgentBrowserTool) -> dict:
    """Check login/CAPTCHA result from snapshot."""
    result = await tool({"action": "snapshot"})
    text = result.output or ""
    return {
        "snapshot_len": len(text),
        "has_slider": "滑块" in text,
        "has_puzzle": "拼图" in text,
        "has_fail": "验证失败" in text,
        "has_success": "验证成功" in text,
        "has_workbench": "工作台" in text,
        "has_password_login": "密码登录" in text,
    }


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
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
        await do_login(tool)

        log("Step 5: Get CAPTCHA positions")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const bottom = document.querySelector('.bottomImage');
                    const drag = document.querySelector('.dragImage');
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const br = bottom ? bottom.getBoundingClientRect() : null;
                    const dr = drag ? drag.getBoundingClientRect() : null;
                    const btr = btn ? btn.getBoundingClientRect() : null;
                    return {
                        bottom: br ? {x: br.x, y: br.y, w: br.width, h: br.height} : null,
                        drag: dr ? {x: dr.x, y: dr.y, w: dr.width, h: dr.height} : null,
                        button: btr ? {x: btr.x, y: btr.y, w: btr.width, h: btr.height} : null
                    };
                })()
            """,
        })
        rects = json.loads(result.output or "{}")
        log(f"  Rects: {rects}")

        bottom_rect = rects.get("bottom")
        drag_rect = rects.get("drag")
        button_rect = rects.get("button")
        if not bottom_rect or not drag_rect or not button_rect:
            log("  Could not find CAPTCHA elements")
            return

        log("Step 6: Take screenshot")
        screenshot_path = os.path.join(download_dir, "captcha.png")
        await tool({"action": "screenshot", "path": screenshot_path})
        log(f"  Screenshot: {screenshot_path}")

        log("Step 7: Analyze gap position")
        gap_x_edge = find_gap_position(screenshot_path, bottom_rect, drag_rect)
        gap_x_color = find_gap_by_color(screenshot_path, bottom_rect, drag_rect)
        log(f"  Edge detection gap: {gap_x_edge}")
        log(f"  Color detection gap: {gap_x_color}")

        # Use edge detection if available, otherwise color, otherwise fallback
        if gap_x_edge is not None:
            gap_x = gap_x_edge
        elif gap_x_color is not None:
            gap_x = gap_x_color
        else:
            gap_x = drag_rect["x"] + 200
            log(f"  Fallback gap: {gap_x}")

        # The drag target should align the LEFT edge of the dragImage with the gap
        # Actually we want the dragImage to cover the gap. The drag piece width is 78px.
        # The gap is a missing piece in the background. We need to align the puzzle piece
        # so it fits. Usually the piece should be centered on or aligned with the gap.
        # Let's try aligning the center of the drag piece with the gap center.
        drag_center = drag_rect["x"] + drag_rect["w"] / 2
        distance = int(gap_x - drag_center)
        log(f"  Gap x={gap_x}, drag center={drag_center}, distance={distance}")

        # Sanity check distance
        if distance < 50 or distance > 300:
            log(f"  Distance {distance} seems wrong, using fallback 180")
            distance = 180
            gap_x = int(drag_center + distance)

        log("Step 8: Perform drag")
        start_x = int(button_rect["x"] + button_rect["w"] / 2)
        start_y = int(button_rect["y"] + button_rect["h"] / 2)
        await perform_drag(tool, start_x, start_y, distance)

        log("Step 9: Check CAPTCHA state")
        state = await get_captcha_state(tool)
        log(f"  State: {state}")

        await asyncio.sleep(3.0)

        log("Step 10: Check result")
        result = await check_result(tool)
        log(f"  Result: {result}")

        # If still showing CAPTCHA, try one more distance
        if result["has_slider"] or result["has_puzzle"]:
            log("Step 11: First attempt failed, trying alternate distance")
            # Reset drag by clicking somewhere else then trying new distance
            # First, let's just try a different gap estimation
            alt_gap_x = gap_x + 20 if gap_x else gap_x_color + 20 if gap_x_color else drag_rect["x"] + 220
            alt_distance = int(alt_gap_x - drag_center)
            if 50 <= alt_distance <= 300:
                log(f"  Trying alternate distance: {alt_distance}")
                # Reset first - move button back to start by clicking the reset/refresh button if any
                # Or just drag back
                back_distance = -distance + alt_distance
                await perform_drag(tool, start_x + distance, start_y, back_distance)
                await asyncio.sleep(2.0)
                result2 = await check_result(tool)
                log(f"  Alt result: {result2}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
