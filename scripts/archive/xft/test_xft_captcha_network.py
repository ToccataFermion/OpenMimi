"""Monitor network requests during CAPTCHA drag to understand validation."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser_args = [
        "--disable-blink-features=AutomationControlled",
    ]
    tool = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        browser_args=browser_args,
    )

    try:
        log("Step 1: Navigate and login")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(1.0)
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)
        await tool({
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
        await asyncio.sleep(3.0)

        log("Step 2: Check CAPTCHA state")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    return {
                        hasButton: !!btn,
                        btnRect: btn ? {x: btn.getBoundingClientRect().x, y: btn.getBoundingClientRect().y, w: btn.getBoundingClientRect().width, h: btn.getBoundingClientRect().height} : null
                    };
                })()
            """,
        })
        state = json.loads(result.output or "{}")
        log(f"  State: {state}")
        if not state.get("hasButton"):
            log("  No CAPTCHA, aborting")
            return

        log("Step 3: Clear network log and start monitoring")
        await tool._exec("network", "requests", "--clear", "--json")

        log("Step 4: Perform drag")
        btn_rect = state["btnRect"]
        start_x = int(btn_rect["x"] + btn_rect["w"] / 2)
        start_y = int(btn_rect["y"] + btn_rect["h"] / 2)
        distance = 180

        await tool._exec("mouse", "move", str(start_x), str(start_y), "--json")
        await asyncio.sleep(0.2)
        await tool._exec("mouse", "down", "--json")
        await asyncio.sleep(0.2)

        for i in range(1, 21):
            t = i / 20
            cx = start_x + int(distance * t)
            cy = start_y + (i % 3 - 1)
            await tool._exec("mouse", "move", str(cx), str(cy), "--json")
            await asyncio.sleep(0.05)

        await tool._exec("mouse", "up", "--json")
        await asyncio.sleep(1.0)

        log("Step 5: Check network requests")
        result = await tool._exec("network", "requests", "--json")
        log(f"  Network: {result.output[:2000] if result.output else 'empty'}")

        log("Step 6: Check page state after drag")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const text = document.querySelector('.imageVerifyDragText');
                    return {
                        btnLeft: btn ? btn.style.left : null,
                        dragLeft: drag ? drag.style.left : null,
                        text: text ? text.textContent.trim().slice(0, 100) : null
                    };
                })()
            """,
        })
        log(f"  After drag: {result.output}")

        log("Step 7: Check result")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Has slider: {'滑块' in text}")
        log(f"  Has puzzle: {'拼图' in text}")
        log(f"  Has fail: {'验证失败' in text}")
        log(f"  Has success: {'验证成功' in text}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
