"""Verify mouse coordinates land on the handle element."""
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
                    if (btn) { btn.click(); return {clicked: true}; }
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
                        viewportX: r.left + r.width/2,
                        viewportY: r.top + r.height/2,
                        screenX_tl: Math.round(sx + r.left),
                        screenY_tl: Math.round(sy + r.top),
                        rect: {left: r.left, top: r.top, width: r.width, height: r.height},
                        window: {screenX: window.screenX, screenY: window.screenY, outerWidth: window.outerWidth, outerHeight: window.outerHeight, innerWidth: window.innerWidth, innerHeight: window.innerHeight},
                    };
                })()
            """,
        })
        pos = json.loads(result.output or "{}")
        log(f"Handle pos: {json.dumps(pos, indent=2)}")

        sx = pos["screenX"]
        sy = pos["screenY"]

        # Move mouse to handle center using computer tool
        log(f"\nMoving mouse to ({sx}, {sy})...")
        await browser({"action": "focus"})
        await asyncio.sleep(0.3)
        await computer({"action": "mouse_move", "coordinate": [sx, sy]})
        await asyncio.sleep(0.5)

        # Check what's under the mouse
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const under = document.elementFromPoint(
                        btn.getBoundingClientRect().left + btn.getBoundingClientRect().width/2,
                        btn.getBoundingClientRect().top + btn.getBoundingClientRect().height/2
                    );
                    return {
                        handleTag: btn ? btn.tagName : null,
                        handleClass: btn ? btn.className : null,
                        underMouseTag: under ? under.tagName : null,
                        underMouseClass: under ? under.className : null,
                        isHandle: under === btn,
                    };
                })()
            """,
        })
        hover = json.loads(result.output or "{}")
        log(f"Hover check: {json.dumps(hover, indent=2)}")

        # Now do a very short drag (10px) and see if handle moves
        log(f"\nDoing mini drag 10px...")
        await computer({
            "action": "mouse_drag",
            "x": sx, "y": sy,
            "end_x": sx + 10, "end_y": sy,
            "steps": 5, "delay_ms": 50,
        })
        await asyncio.sleep(0.5)

        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    return {
                        btnLeft: btn ? window.getComputedStyle(btn).left : null,
                        dragLeft: drag ? window.getComputedStyle(drag).left : null,
                    };
                })()
            """,
        })
        after = json.loads(result.output or "{}")
        log(f"After mini drag: {json.dumps(after, indent=2)}")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
