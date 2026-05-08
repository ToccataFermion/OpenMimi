"""Try 222px drag with longer wait and better diagnostics."""
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
        log("Step 1: Navigate")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(1.0)

        log("Step 2: Click login")
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Step 3: Fill credentials")
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
        await asyncio.sleep(3.0)

        log("Step 5: Inspect page state")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const modal = document.querySelector('.ant-modal-content');
                    const verify = document.querySelector('.xftImageVerify');
                    return {
                        hasButton: !!btn,
                        hasModal: !!modal,
                        hasVerify: !!verify,
                        btnRect: btn ? {x: btn.getBoundingClientRect().x, y: btn.getBoundingClientRect().y, w: btn.getBoundingClientRect().width, h: btn.getBoundingClientRect().height} : null
                    };
                })()
            """,
        })
        state = json.loads(result.output or "{}")
        log(f"  State: {state}")

        if not state.get("hasButton"):
            log("  No CAPTCHA button found, aborting")
            # Take snapshot to see what's there
            result = await tool({"action": "snapshot"})
            log(f"  Snapshot (first 200 chars): {result.output[:200] if result.output else 'empty'}")
            return

        btn_rect = state["btnRect"]
        start_x = int(btn_rect["x"] + btn_rect["w"] / 2)
        start_y = int(btn_rect["y"] + btn_rect["h"] / 2)
        distance = 222

        log(f"Step 6: Drag button from ({start_x}, {start_y}) by {distance}px")
        await tool._exec("mouse", "move", str(start_x), str(start_y), "--json")
        await asyncio.sleep(0.2)
        await tool._exec("mouse", "down", "--json")
        await asyncio.sleep(0.2)

        steps = 30
        for i in range(1, steps + 1):
            t = i / steps
            ease = 1 - (1 - t) ** 3
            cx = start_x + int(distance * ease)
            cy = start_y + (i % 3 - 1)
            await tool._exec("mouse", "move", str(cx), str(cy), "--json")
            await asyncio.sleep(0.03 + (i % 5) * 0.005)

        await tool._exec("mouse", "up", "--json")
        await asyncio.sleep(0.3)
        log("  Drag complete")

        await asyncio.sleep(3.0)

        log("Step 7: Check result")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Snapshot len: {len(text)}")
        log(f"  Contains '滑块': {'滑块' in text}")
        log(f"  Contains '拼图': {'拼图' in text}")
        log(f"  Contains '验证失败': {'验证失败' in text}")
        log(f"  Contains '验证成功': {'验证成功' in text}")
        log(f"  Contains '工作台': {'工作台' in text}")
        log(f"  Contains '密码登录': {'密码登录' in text}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
