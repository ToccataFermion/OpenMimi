"""Try multiple drag distances to find one that works for xft CAPTCHA."""
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

try:
    import win32gui
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def find_browser_window():
    candidates = []
    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if "Chrome" in title:
                rect = win32gui.GetWindowRect(hwnd)
                candidates.append((hwnd, title, rect))
        return True
    win32gui.EnumWindows(callback, None)
    return candidates[-1] if candidates else None


def focus_window(hwnd):
    try:
        win32gui.SetForegroundWindow(hwnd)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception as exc:
        log(f"  Focus warning: {exc}")


async def get_captcha_data(browser):
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.imageVerifyDragButton');
                const bg = document.querySelector('.bottomImage');
                function rectInfo(el) {
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {left: r.left, top: r.top, width: r.width, height: r.height};
                }
                return {
                    hasButton: !!btn,
                    hasBg: !!bg,
                    btnRect: rectInfo(btn),
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
    return json.loads(result.output or "{}")


async def check_login_success(browser):
    """Return True if we're past the login/CAPTCHA screen."""
    result = await browser({"action": "snapshot"})
    text = result.output or ""
    # If we see workbench/dashboard indicators, we're in
    success_indicators = ["工作台", "首页", "dashboard", "logout", "退出"]
    for ind in success_indicators:
        if ind in text:
            return True
    # If we don't see the login form, might be success or loading
    if "手机号登录" not in text and "密码登录" not in text:
        return True
    return False


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
        await browser({
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
        await asyncio.sleep(0.5)

        # Try multiple distances
        distances = [150, 200, 250, 280, 300, 320, 340]

        for attempt, distance in enumerate(distances):
            log(f"\n=== Attempt {attempt + 1} with distance={distance} ===")

            log("  Click login button")
            await browser({
                "action": "eval",
                "js": """
                    (() => {
                        const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                        if (btn) {
                            ['mousedown', 'mouseup', 'click'].forEach(type => {
                                btn.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                            });
                            return {clicked: true};
                        }
                        return {clicked: false};
                    })()
                """,
            })
            await asyncio.sleep(4.0)

            data = await get_captcha_data(browser)
            if not data.get("hasButton"):
                log("  No CAPTCHA appeared - checking if logged in")
                if await check_login_success(browser):
                    log("  SUCCESS: Logged in!")
                    return
                log("  Still on login form, will retry")
                continue

            sx = data.get("screenX", 0)
            sy = data.get("screenY", 0)
            ow = data.get("outerWidth", 1280)
            oh = data.get("outerHeight", 800)
            iw = data.get("innerWidth", 1280)
            ih = data.get("innerHeight", 800)
            left_frame = (ow - iw) // 2
            top_frame = oh - ih - left_frame

            btn_rect = data.get("btnRect")
            bg_rect = data.get("bgRect")
            if not btn_rect:
                log("  No button rect")
                continue

            start_vx = btn_rect["left"] + btn_rect["width"] / 2
            start_vy = btn_rect["top"] + btn_rect["height"] / 2
            start_sx = int(sx + left_frame + start_vx)
            start_sy = int(sy + top_frame + start_vy)

            end_vx = start_vx + distance
            end_vy = start_vy + random.randint(-3, 3)
            end_sx = int(sx + left_frame + end_vx)
            end_sy = int(sy + top_frame + end_vy)

            log(f"  Screen drag: ({start_sx}, {start_sy}) -> ({end_sx}, {end_sy})")

            win_info = find_browser_window()
            if win_info:
                focus_window(win_info[0])
                await asyncio.sleep(0.5)

            # More human-like drag: hover, down, slow move, up
            await computer({"action": "mouse_move", "x": start_sx, "y": start_sy})
            await asyncio.sleep(0.3)
            await computer({"action": "mouse_down", "button": "left"})
            await asyncio.sleep(0.2)

            # Manual bezier drag with variable speed
            cx = (start_sx + end_sx) // 2 + random.randint(-40, 40)
            cy = (start_sy + end_sy) // 2 + random.randint(-15, 15)
            steps = 40
            for i in range(1, steps + 1):
                t = i / steps
                x = int((1 - t) ** 2 * start_sx + 2 * (1 - t) * t * cx + t ** 2 * end_sx)
                y = int((1 - t) ** 2 * start_sy + 2 * (1 - t) * t * cy + t ** 2 * end_sy)
                x += random.randint(-2, 2)
                y += random.randint(-2, 2)
                await computer({"action": "mouse_move", "x": x, "y": y})
                # Variable delay: start slow, speed up in middle, slow down at end
                if i < steps * 0.2 or i > steps * 0.8:
                    await asyncio.sleep(0.02)
                else:
                    await asyncio.sleep(0.01)

            await computer({"action": "mouse_move", "x": end_sx, "y": end_sy})
            await asyncio.sleep(0.3)
            await computer({"action": "mouse_up", "button": "left"})
            await asyncio.sleep(2.0)

            if await check_login_success(browser):
                log(f"  SUCCESS with distance={distance}!")
                return

            log(f"  Failed with distance={distance}")

        log("\nAll distances exhausted. CAPTCHA not solved.")

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
