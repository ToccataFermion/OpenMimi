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
        print(f"Snapshot:\n{result.output[:2000]}")
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
        print(f"Snapshot:\n{result.output[:2000]}")

        print("\n" + "=" * 60)
        print("Step 5: Click '密码登录' by text")
        print("=" * 60)
        result = await tool({"action": "click", "target_text": "密码登录"})
        print(f"Result: {result.output}")

        print("\n" + "=" * 60)
        print("Step 6: Click consent checkbox")
        print("=" * 60)
        result = await tool({"action": "click", "target_text": "我已阅读并同意"})
        print(f"Result: {result.output}")

        print("\n" + "=" * 60)
        print("Step 7: Fill credentials via eval")
        print("=" * 60)
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    function setNativeValue(el, val) {
                        if (!el) return;
                        nativeSetter.call(el, val);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    const inputs = Array.from(document.querySelectorAll('input')).filter(i => {
                        const r = i.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    });
                    const phone = inputs.find(i => i.type === 'text');
                    const pass = inputs.find(i => i.type === 'password');
                    const cb = inputs.find(i => i.type === 'checkbox');
                    setNativeValue(phone, '18584828398');
                    setNativeValue(pass, 'Liszt123');
                    if (cb) { cb.checked = true; cb.dispatchEvent(new Event('change', {bubbles: true})); }
                    return {phone: phone ? phone.value : null, pass: pass ? pass.value : null};
                })()
            """,
        })
        print(f"Result: {result.output}")

        print("\n" + "=" * 60)
        print("Step 8: Click login button by text")
        print("=" * 60)
        result = await tool({"action": "click", "target_text": "登录"})
        print(f"Result: {result.output}")

        print("\n" + "=" * 60)
        print("Step 9: Wait and snapshot for CAPTCHA or dashboard")
        print("=" * 60)
        await tool({"action": "wait", "milliseconds": 3000})
        result = await tool({"action": "snapshot"})
        print(f"Snapshot:\n{result.output[:2000]}")

        print("\n" + "=" * 60)
        print("Step 10: Click customer service icon")
        print("=" * 60)
        # Try clicking at (997, 24) which we found earlier
        result = await tool({"action": "click", "target_text": "客服"})
        print(f"Result: {result.output}")

        await tool({"action": "wait", "milliseconds": 2000})
        result = await tool({"action": "snapshot"})
        print(f"Snapshot after CS click:\n{result.output[:2000]}")

        print("\n" + "=" * 60)
        print("Step 11: Click 在线客服")
        print("=" * 60)
        result = await tool({"action": "click", "target_text": "在线客服"})
        print(f"Result: {result.output}")

        await tool({"action": "wait", "milliseconds": 2000})
        result = await tool({"action": "snapshot"})
        print(f"Final snapshot:\n{result.output[:2000]}")

        print("\nKeeping browser open for 10s...")
        await asyncio.sleep(10.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
