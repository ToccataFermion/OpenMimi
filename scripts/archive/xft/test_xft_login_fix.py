"""Test xft login with correct input targeting."""
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

        log("Step 3: Fill inputs via eval using correct selectors")
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

                    if (phone) {
                        phone.focus();
                        setReactValue(phone, '18584828398');
                    }
                    if (pass) {
                        pass.focus();
                        setReactValue(pass, 'Liszt123');
                    }
                    if (checkbox && !checkbox.checked) {
                        checkbox.click();
                    }

                    return {
                        phoneFound: !!phone,
                        passFound: !!pass,
                        checkboxFound: !!checkbox,
                        phoneValue: phone?.value,
                        passValue: pass?.value,
                        checkboxChecked: checkbox?.checked
                    };
                })()
            """,
        })
        log(f"  Fill result: {result.output}")
        await asyncio.sleep(1.0)

        log("Step 4: Check login button state")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                    return {
                        found: !!btn,
                        class: btn?.className,
                        disabled: btn?.className?.includes('disabled'),
                        text: btn?.textContent?.trim()
                    };
                })()
            """,
        })
        log(f"  Button state: {result.output}")

        log("Step 5: Click login button via eval")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                    if (!btn) return {error: 'button not found'};
                    btn.focus();
                    ['mousedown', 'mouseup', 'click'].forEach(type => {
                        btn.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                    });
                    return {clicked: true, class: btn.className};
                })()
            """,
        })
        log(f"  Click result: {result.output}")
        await asyncio.sleep(3.0)

        log("Step 6: Snapshot")
        result = await tool({"action": "snapshot"})
        has_login = "密码登录" in (result.output or "")
        log(f"  Login dialog still visible: {has_login}")
        if not has_login:
            log("  SUCCESS! Login dialog closed.")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
