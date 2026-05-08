"""Test if CAPTCHA validates after waiting longer post-drag."""
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
                    };
                })()
            """,
        })
        pos = json.loads(result.output or "{}")
        sx, sy = pos["screenX"], pos["screenY"]
        log(f"  Handle screen: ({sx}, {sy})")

        # Focus and drag
        log("\n=== Drag 150px ===")
        await browser({"action": "focus"})
        await asyncio.sleep(0.3)
        await computer({
            "action": "mouse_drag",
            "x": sx, "y": sy,
            "end_x": sx + 150, "end_y": sy,
            "steps": 80, "delay_ms": 25,
        })

        log("  Waiting 10 seconds for validation...")
        for i in range(10):
            await asyncio.sleep(1.0)
            result = await browser({
                "action": "eval",
                "js": """
                    (() => {
                        const btn = document.querySelector('.imageVerifyDragButton');
                        const drag = document.querySelector('.dragImage');
                        const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                        const msg = document.querySelector('.verify-msg') || document.querySelector('.captcha-msg');
                        return {
                            hasVerify: !!verify,
                            verifyDisplay: verify ? window.getComputedStyle(verify).display : null,
                            btnLeft: btn ? window.getComputedStyle(btn).left : null,
                            dragLeft: drag ? window.getComputedStyle(drag).left : null,
                            msg: msg ? msg.textContent : null,
                            title: document.title,
                        };
                    })()
                """,
            })
            state = json.loads(result.output or "{}")
            log(f"  t={i+1}s: {json.dumps(state, ensure_ascii=False)}")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
