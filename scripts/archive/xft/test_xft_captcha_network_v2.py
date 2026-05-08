"""Monitor network requests during CAPTCHA drag - save to file and filter."""
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

        log("Step 2: Clear network and perform drag")
        await tool._exec("network", "requests", "--clear", "--json")

        # Simple drag
        await tool._exec("mouse", "move", "489", "462", "--json")
        await asyncio.sleep(0.2)
        await tool._exec("mouse", "down", "--json")
        await asyncio.sleep(0.2)
        await tool._exec("mouse", "move", "600", "462", "--json")
        await asyncio.sleep(0.3)
        await tool._exec("mouse", "up", "--json")
        await asyncio.sleep(2.0)

        log("Step 3: Get network requests")
        result = await tool._exec("network", "requests", "--json")
        network_data = result.output or "[]"

        # Save to file
        network_path = os.path.join(download_dir, "network.json")
        with open(network_path, "w", encoding="utf-8") as f:
            f.write(network_data)
        log(f"  Saved network data to {network_path}")

        # Parse and filter
        try:
            requests = json.loads(network_data)
            log(f"  Total requests: {len(requests)}")
            keywords = ["captcha", "verify", "validate", "slide", "check", "auth", "login", "token", "challenge"]
            interesting = []
            for req in requests:
                url = req.get("url", "").lower()
                if any(k in url for k in keywords):
                    interesting.append({
                        "url": req.get("url"),
                        "method": req.get("method"),
                        "status": req.get("status"),
                        "timing": req.get("timing"),
                    })
            log(f"  Interesting requests: {len(interesting)}")
            for req in interesting[:20]:
                log(f"    {req['method']} {req['status']} {req['url'][:120]}")
        except Exception as e:
            log(f"  Error parsing network data: {e}")

        log("Step 4: Check result")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Has slider: {'滑块' in text}")
        log(f"  Has puzzle: {'拼图' in text}")
        log(f"  Has fail: {'验证失败' in text}")
        log(f"  Has success: {'验证成功' in text}")

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
