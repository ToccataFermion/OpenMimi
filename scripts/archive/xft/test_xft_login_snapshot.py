"""Test xft login and capture snapshot after click to inspect page state."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    tool = AgentBrowserTool(download_dir=download_dir, viewport=(1280, 800))

    try:
        log("Step 1: Navigate")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})

        log("Step 2: Click login")
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Step 3: Fill inputs")
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

                    return {phone: phone?.value, pass: pass?.value, checked: checkbox?.checked};
                })()
            """,
        })
        log(f"  Fill: {result.output}")
        await asyncio.sleep(0.5)

        log("Step 4: Get snapshot before click")
        result = await tool({"action": "snapshot"})
        before = result.output or ""
        log(f"  Before length: {len(before)}")

        log("Step 5: Click login button")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                    if (btn) {
                        btn.scrollIntoView({behavior: 'instant', block: 'center'});
                        btn.focus();
                        const rect = btn.getBoundingClientRect();
                        const x = rect.left + rect.width / 2;
                        const y = rect.top + rect.height / 2;
                        ['mousedown', 'mouseup', 'click'].forEach(type => {
                            const ev = new MouseEvent(type, {
                                bubbles: true,
                                cancelable: true,
                                view: window,
                                clientX: x,
                                clientY: y
                            });
                            btn.dispatchEvent(ev);
                        });
                        // Also try clicking via parent form submit if exists
                        const form = btn.closest('form');
                        if (form) form.dispatchEvent(new Event('submit', {bubbles: true}));
                        return {clicked: true, x, y, class: btn.className};
                    }
                    return {clicked: false};
                })()
            """,
        })
        log(f"  Click: {result.output}")
        await asyncio.sleep(3.0)

        log("Step 6: Get snapshot after click")
        result = await tool({"action": "snapshot"})
        after = result.output or ""
        log(f"  After length: {len(after)}")

        # Save snapshots for comparison
        with open(os.path.join(download_dir, "snapshot_before.txt"), "w", encoding="utf-8") as f:
            f.write(before)
        with open(os.path.join(download_dir, "snapshot_after.txt"), "w", encoding="utf-8") as f:
            f.write(after)
        log(f"  Snapshots saved to {download_dir}")

        # Check for key indicators
        log(f"  Contains '密码登录': {'密码登录' in after}")
        log(f"  Contains '登录失败': {'登录失败' in after}")
        log(f"  Contains '验证码': {'验证码' in after}")
        log(f"  Contains '错误': {'错误' in after}")
        log(f"  Contains '工作台': {'工作台' in after}")
        log(f"  Contains '首页': {'首页' in after}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
