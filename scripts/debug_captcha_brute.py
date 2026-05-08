"""Brute-force test different drag distances on the same CAPTCHA instance."""
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


async def try_drag(computer: ComputerTool, browser: AgentBrowserTool,
                   label: str, sx: int, sy: int, distance: int) -> dict:
    log(f"\n--- Try {label}: drag {distance}px ---")
    await browser({"action": "focus"})
    await asyncio.sleep(0.3)
    await computer({
        "action": "mouse_drag",
        "x": sx, "y": sy,
        "end_x": sx + distance, "end_y": sy,
        "steps": 80, "delay_ms": 25,
    })
    await asyncio.sleep(1.0)
    state = await check_captcha(browser)
    log(f"  State: {json.dumps(state)}")
    return state


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
                    if (btn) { btn.click(); return {clicked: true, class: btn.className}; }
                    return {clicked: false};
                })()
            """,
        })
        await asyncio.sleep(4.0)

        # Get handle position
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const r = btn.getBoundingClientRect();
                    const sx = window.screenX + (window.outerWidth - window.innerWidth) / 2;
                    const sy = window.screenY + window.outerHeight - window.innerHeight - (window.outerWidth - window.innerWidth) / 2;
                    return {
                        screenX: Math.round(sx + r.left + r.width/2),
                        screenY: Math.round(sy + r.top + r.height/2),
                        btnLeft: r.left,
                        btnWidth: r.width,
                    };
                })()
            """,
        })
        pos = json.loads(result.output or "{}")
        sx = pos["screenX"]
        sy = pos["screenY"]
        log(f"  Handle screen: ({sx}, {sy})")

        baseline = await check_captcha(browser)
        log(f"  Baseline: {json.dumps(baseline)}")

        # Try a range of distances
        for dist in [50, 100, 120, 140, 160, 180, 200, 220, 240, 260]:
            state = await try_drag(computer, browser, f"dist={dist}", sx, sy, dist)
            if not state.get("hasVerify"):
                log(f"  SUCCESS! CAPTCHA solved at distance {dist}")
                break
            # Reset handle to 0 by dragging back
            log(f"  Resetting handle to 0...")
            current_left = float(state.get("btnLeft", "0px").replace("px", ""))
            if current_left > 0:
                await browser({"action": "focus"})
                await asyncio.sleep(0.2)
                await computer({
                    "action": "mouse_drag",
                    "x": sx + int(current_left), "y": sy,
                    "end_x": sx, "end_y": sy,
                    "steps": 40, "delay_ms": 15,
                })
                await asyncio.sleep(0.5)
        else:
            log("  No distance worked.")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
