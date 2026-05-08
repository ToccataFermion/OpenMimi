"""Test xft login with AgentBrowserTool.

Usage:
    cd D:\Programs\projects\OpenMimi
    .venv/Scripts/python.exe scripts/test_agent_browser_xft.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool


def safe_print(label: str, text: str | None, max_len: int = 2000) -> None:
    """Print text safely handling Windows console encoding issues."""
    text = text or ""
    snippet = text[:max_len]
    try:
        print(f"{label}\n{snippet}")
    except UnicodeEncodeError:
        # Encode to GBK with replacements, then decode back for printing
        safe = snippet.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace")
        print(f"{label}\n{safe}")


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    tool = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
    )

    try:
        print("=" * 60)
        print("Step 1: Navigate to xft.cmbchina.com")
        print("=" * 60)
        result = await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        print(f"Result: {result.output[:500]}")
        print(f"Details: {result.details}")

        print("\n" + "=" * 60)
        print("Step 2: Snapshot to discover elements")
        print("=" * 60)
        result = await tool({"action": "snapshot"})
        safe_print("Snapshot:", result.output)
        print(f"Refs: {result.details.get('refs', {})}")

        print("\n" + "=" * 60)
        print("Step 3: Click '登录' by text")
        print("=" * 60)
        result = await tool({"action": "click", "target_text": "登录"})
        print(f"Result: {result.output}")
        print(f"Details: {result.details}")

        print("\n" + "=" * 60)
        print("Step 4: Snapshot popup")
        print("=" * 60)
        result = await tool({"action": "snapshot"})
        safe_print("Snapshot:", result.output)

        print("\n" + "=" * 60)
        print("Step 5: Click '密码登录' by text")
        print("=" * 60)
        result = await tool({"action": "click", "target_text": "密码登录"})
        print(f"Result: {result.output}")

        print("\n" + "=" * 60)
        print("Step 6: Check consent checkbox with check action")
        print("=" * 60)
        result = await tool({"action": "check", "target_text": "我已阅读并同意"})
        print(f"Result: {result.output}")

        print("\n" + "=" * 60)
        print("Step 7: Fill credentials via eval (native setter)")
        print("=" * 60)
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
        print(f"Fill result: {result.output}")

        print("\n" + "=" * 60)
        print("Step 8: Click login button via eval (React SPA compatible)")
        print("=" * 60)
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
                    return {clicked: true, class: btn.className?.slice(0, 40)};
                })()
            """,
        })
        print(f"Click result: {result.output}")
        await asyncio.sleep(2.0)

        print("\n" + "=" * 60)
        print("Step 9: Wait and snapshot for CAPTCHA or dashboard")
        print("=" * 60)
        await asyncio.sleep(3.0)
        result = await tool({"action": "snapshot"})
        safe_print("Snapshot:", result.output)
        if result.is_error and result.details and result.details.get("captcha_detected"):
            print("\n[CAPTCHA DETECTED] Human intervention required to complete slider puzzle.")

        print("\n" + "=" * 60)
        print("Step 10: Click customer service icon")
        print("=" * 60)
        # Try clicking at (997, 24) which we found earlier
        result = await tool({"action": "click", "target_text": "客服"})
        print(f"Result: {result.output}")

        await tool({"action": "wait", "milliseconds": 2000})
        result = await tool({"action": "snapshot"})
        safe_print("Snapshot after CS click:", result.output)

        print("\n" + "=" * 60)
        print("Step 11: Click 在线客服")
        print("=" * 60)
        result = await tool({"action": "click", "target_text": "在线客服"})
        print(f"Result: {result.output}")

        await tool({"action": "wait", "milliseconds": 2000})
        result = await tool({"action": "snapshot"})
        safe_print("Final snapshot:", result.output)

        print("\nKeeping browser open for 10s...")
        await asyncio.sleep(10.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
