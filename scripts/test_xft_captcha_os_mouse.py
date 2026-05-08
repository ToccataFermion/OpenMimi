"""Try to solve xft slider CAPTCHA using OS-level mouse via ComputerTool."""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool
from openmimi.tools.computer import ComputerTool

# win32gui is available on Windows
try:
    import win32gui
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def find_browser_window():
    """Find the visible Chrome window launched by agent-browser."""
    candidates = []

    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            # Look for Chrome windows
            if "Chrome" in title:
                rect = win32gui.GetWindowRect(hwnd)
                candidates.append((hwnd, title, rect))
        return True

    win32gui.EnumWindows(callback, None)
    # Prefer the most recently created / most relevant window
    # Usually the last one in the list is the newest
    return candidates[-1] if candidates else None


def focus_window(hwnd):
    """Bring window to foreground."""
    try:
        win32gui.SetForegroundWindow(hwnd)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception as exc:
        log(f"  Focus warning: {exc}")


def bezier_points(start: tuple[int, int], end: tuple[int, int], steps: int = 30):
    """Generate points along a bezier curve with random jitter."""
    sx, sy = start
    ex, ey = end
    cx = (sx + ex) // 2 + random.randint(-30, 30)
    cy = (sy + ey) // 2 + random.randint(-10, 10)
    points = []
    for i in range(steps + 1):
        t = i / steps
        x = int((1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * ex)
        y = int((1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * ey)
        x += random.randint(-1, 1)
        y += random.randint(-1, 1)
        points.append((x, y))
    return points


async def main() -> None:
    if not HAS_WIN32:
        log("win32gui not available, aborting")
        return

    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser_args = ["--disable-blink-features=AutomationControlled"]
    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=browser_args,
    )
    computer = ComputerTool()

    try:
        log("Step 1: Navigate to xft")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)

        log("Step 2: Click login tab")
        await browser({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Step 3: Fill credentials")
        result = await browser({
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
        log(f"  Fill: {result.output}")
        await asyncio.sleep(0.5)

        log("Step 4: Click login button")
        result = await browser({
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
                        return {clicked: true, cx: rect.left + rect.width/2, cy: rect.top + rect.height/2};
                    }
                    return {clicked: false};
                })()
            """,
        })
        log(f"  Login click: {result.output}")
        await asyncio.sleep(4.0)

        log("Step 5: Check CAPTCHA and get coordinates")
        result = await browser({
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
                        screenX: window.screenX,
                        screenY: window.screenY,
                        innerWidth: window.innerWidth,
                        innerHeight: window.innerHeight,
                        outerWidth: window.outerWidth,
                        outerHeight: window.outerHeight,
                    };
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"  CAPTCHA: {json.dumps(data, indent=2)[:1000]}")

        if not data.get("hasButton"):
            log("  No CAPTCHA - checking login state")
            result = await browser({"action": "snapshot"})
            log(f"  Page: {result.output[:300] if result.output else 'empty'}")
            return

        # Calculate screen coordinates
        sx = data.get("screenX", 0)
        sy = data.get("screenY", 0)
        ow = data.get("outerWidth", 1280)
        oh = data.get("outerHeight", 800)
        iw = data.get("innerWidth", 1280)
        ih = data.get("innerHeight", 800)

        # Estimate frame sizes
        left_frame = (ow - iw) // 2
        top_frame = oh - ih - left_frame
        log(f"  Frame offset: left={left_frame}, top={top_frame}")

        btn_rect = data.get("btnRect")
        if not btn_rect:
            log("  No button rect, aborting")
            return

        # Start position: center of drag button
        start_vx = btn_rect["left"] + btn_rect["width"] / 2
        start_vy = btn_rect["top"] + btn_rect["height"] / 2
        start_sx = int(sx + left_frame + start_vx)
        start_sy = int(sy + top_frame + start_vy)

        # Target position: drag to near right edge of background track
        bg_rect = data.get("bgRect")
        if bg_rect:
            # Drag from button center to near the right edge of bg minus half button width
            end_vx = bg_rect["left"] + bg_rect["width"] - btn_rect["width"] / 2 - 5
            end_vy = start_vy + random.randint(-2, 2)
        else:
            distance = 250
            end_vx = start_vx + distance
            end_vy = start_vy
        end_sx = int(sx + left_frame + end_vx)
        end_sy = int(sy + top_frame + end_vy)

        log(f"  Viewport drag: ({start_vx:.0f}, {start_vy:.0f}) -> ({end_vx:.0f}, {end_vy:.0f})")
        log(f"  Screen drag:   ({start_sx}, {start_sy}) -> ({end_sx}, {end_sy})")

        log("Step 6: Focus browser window")
        win_info = find_browser_window()
        if win_info:
            hwnd, title, rect = win_info
            log(f"  Found window: {title} at {rect}")
            focus_window(hwnd)
            await asyncio.sleep(0.5)
        else:
            log("  Warning: could not find browser window")

        log("Step 7: OS-level mouse drag using ComputerTool")
        result = await computer({"action": "screenshot"})
        log(f"  Screenshot before drag: {result.output}")

        await computer({
            "action": "mouse_drag",
            "x": start_sx,
            "y": start_sy,
            "end_x": end_sx,
            "end_y": end_sy,
            "button": "left",
            "steps": 30,
        })
        await asyncio.sleep(0.5)

        result = await computer({"action": "screenshot"})
        log(f"  Screenshot after drag: {result.output}")

        log("Step 8: Check login state after CAPTCHA")
        await asyncio.sleep(2.0)
        result = await browser({"action": "snapshot"})
        log(f"  Page after drag: {result.output[:500] if result.output else 'empty'}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
