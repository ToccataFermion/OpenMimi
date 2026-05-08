"""Test xft login with working eval (no return keyword)."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    tool = AgentBrowserTool(download_dir=download_dir, viewport=(1280, 800))

    try:
        print("Step 1: Navigate")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})

        print("Step 2: Click login")
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        print("Step 3: Inspect DOM for editable elements")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const editable = all.filter(el => {
                        const t = el.tagName;
                        const r = el.getAttribute('role');
                        return t === 'INPUT' || t === 'TEXTAREA' || r === 'textbox' || el.isContentEditable;
                    });
                    return editable.map(el => ({
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        class: el.className?.slice(0, 40),
                        type: el.type,
                        placeholder: el.getAttribute('placeholder')?.slice(0, 20),
                        text: el.textContent?.slice(0, 20)
                    }));
                })()
            """,
        })
        print(f"  Editable elements: {result.output}")

        print("Step 4: Fill credentials via eval")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const textboxes = all.filter(el => el.getAttribute('role') === 'textbox');
                    const phone = textboxes[0];
                    const pass = textboxes[1];
                    if (phone) {
                        phone.focus();
                        phone.textContent = '18584828398';
                        phone.dispatchEvent(new Event('input', {bubbles: true}));
                        phone.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    if (pass) {
                        pass.focus();
                        pass.textContent = 'Liszt123';
                        pass.dispatchEvent(new Event('input', {bubbles: true}));
                        pass.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    return {phoneSet: !!phone, passSet: !!pass, phoneText: phone?.textContent, passText: pass?.textContent};
                })()
            """,
        })
        print(f"  Fill result: {result.output}")

        print("Step 5: Check agreement")
        await tool({"action": "check", "ref": "@e20"})

        print("Step 6: Click login")
        await tool({"action": "click", "ref": "@e21"})
        await asyncio.sleep(3.0)

        print("Step 7: Snapshot")
        result = await tool({"action": "snapshot"})
        has_login = "密码登录" in (result.output or "")
        print(f"  Login dialog still visible: {has_login}")

        if has_login:
            print("Step 8: Try focus password then Enter")
            await tool({"action": "hover", "ref": "@e23"})
            await asyncio.sleep(0.5)
            await tool({"action": "press", "key": "Enter"})
            await asyncio.sleep(3.0)
            result = await tool({"action": "snapshot"})
            has_login = "密码登录" in (result.output or "")
            print(f"  Login dialog still visible: {has_login}")

        print("\nKeeping browser open for 10s...")
        await asyncio.sleep(10.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
