"""Try agent-browser's native drag command."""
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

        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    return {hasButton: !!btn};
                })()
            """,
        })
        if not json.loads(result.output or "{}").get("hasButton"):
            log("  No CAPTCHA, aborting")
            return

        log("Step 2: Get button position for drag target")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const container = document.querySelector('.imageVerifyDrag');
                    const cr = container.getBoundingClientRect();
                    const br = btn.getBoundingClientRect();
                    return {
                        btnX: br.x,
                        btnY: br.y,
                        btnW: br.width,
                        btnH: br.height,
                        containerW: cr.width
                    };
                })()
            """,
        })
        pos = json.loads(result.output or "{}")
        log(f"  Positions: {pos}")

        start_x = int(pos["btnX"] + pos["btnW"] / 2)
        start_y = int(pos["btnY"] + pos["btnH"] / 2)
        end_x = start_x + 200
        end_y = start_y

        log(f"Step 3: Try agent-browser drag from ({start_x},{start_y}) to ({end_x},{end_y})")
        # Try: drag .imageVerifyDragButton coordinate
        result = await tool._exec("drag", ".imageVerifyDragButton", f"{end_x},{end_y}", "--json")
        log(f"  Drag result: {result.output[:500] if result.output else 'empty'}")

        await asyncio.sleep(3.0)

        log("Step 4: Check result")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Has slider: {'滑块' in text}")
        log(f"  Has puzzle: {'拼图' in text}")
        log(f"  Has fail: {'验证失败' in text}")
        log(f"  Has success: {'验证成功' in text}")
        log(f"  Has workbench: {'工作台' in text}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
