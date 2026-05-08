"""Fine-grained brute-force test with 5px increments and fresh CAPTCHA per attempt."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool
from openmimi.tools.computer import ComputerTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def get_handle_pos(browser: AgentBrowserTool) -> tuple[int, int] | None:
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.imageVerifyDragButton');
                if (!btn) return null;
                const r = btn.getBoundingClientRect();
                const sx = window.screenX + (window.outerWidth - window.innerWidth) / 2;
                const sy = window.screenY + window.outerHeight - window.innerHeight - (window.outerWidth - window.innerWidth) / 2;
                return {
                    screenX: Math.round(sx + r.left + r.width/2),
                    screenY: Math.round(sy + r.top + r.height/2),
                };
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    if "screenX" not in data:
        return None
    return data["screenX"], data["screenY"]


async def check_captcha(browser: AgentBrowserTool) -> dict:
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.imageVerifyDragButton');
                const drag = document.querySelector('.dragImage');
                const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                return {
                    hasVerify: !!verify,
                    btnLeft: btn ? window.getComputedStyle(btn).left : null,
                    dragLeft: drag ? window.getComputedStyle(drag).left : null,
                };
            })()
        """,
    })
    return json.loads(result.output or "{}")


async def try_distance(computer: ComputerTool, browser: AgentBrowserTool,
                       sx: int, sy: int, dist: int) -> bool:
    log(f"\n--- Trying {dist}px ---")
    await browser({"action": "focus"})
    await asyncio.sleep(0.3)
    await computer({
        "action": "mouse_drag",
        "x": sx, "y": sy,
        "end_x": sx + dist, "end_y": sy,
        "steps": 80, "delay_ms": 25,
    })
    await asyncio.sleep(1.5)
    state = await check_captcha(browser)
    log(f"  State: {json.dumps(state)}")
    if not state.get("hasVerify"):
        log(f"  SUCCESS at {dist}px!")
        return True
    # Wait a bit more to see if it resets
    await asyncio.sleep(2.0)
    state2 = await check_captcha(browser)
    log(f"  State after 3.5s: {json.dumps(state2)}")
    if not state2.get("hasVerify"):
        log(f"  SUCCESS at {dist}px (delayed)!")
        return True
    return False


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=["--disable-blink-features=AutomationControlled"],
    )
    computer = ComputerTool()

    try:
        log("=== Login ===")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)
        await browser({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)
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
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                    if (btn) { btn.click(); return {clicked: true}; }
                    return {clicked: false};
                })()
            """,
        })
        await asyncio.sleep(4.0)

        pos = await get_handle_pos(browser)
        if not pos:
            log("CAPTCHA not found")
            return
        sx, sy = pos
        log(f"  Handle screen: ({sx}, {sy})")

        # Try fine increments
        for dist in range(80, 241, 5):
            if await try_distance(computer, browser, sx, sy, dist):
                break
            # CAPTCHA likely reset itself; get fresh handle position
            fresh_pos = await get_handle_pos(browser)
            if fresh_pos:
                sx, sy = fresh_pos
                log(f"  Fresh handle: ({sx}, {sy})")
            await asyncio.sleep(1.0)
        else:
            log("\nNo distance worked in 80-240 range with 5px steps.")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
