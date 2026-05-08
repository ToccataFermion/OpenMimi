"""Test different submission methods for xft login."""
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

        print("Step 3: Fill fields")
        await tool({"action": "fill", "ref": "@e22", "value": "18584828398"})
        await tool({"action": "fill", "ref": "@e23", "value": "Liszt123"})
        await tool({"action": "check", "ref": "@e20"})

        print("Step 4: Verify field values via eval")
        result = await tool({
            "action": "eval",
            "js": "return document.querySelectorAll('input').length;"
        })
        print(f"  Input count: {result.output}")

        result = await tool({
            "action": "eval",
            "js": "return Array.from(document.querySelectorAll('input')).map(i => ({type: i.type, name: i.name, val: i.value, ph: i.placeholder}));"
        })
        print(f"  Inputs: {result.output}")

        print("Step 5: Try method A - click @e21")
        await tool({"action": "click", "ref": "@e21"})
        await asyncio.sleep(2.0)
        result = await tool({"action": "snapshot"})
        has_login = "密码登录" in (result.output or "")
        print(f"  Login dialog still visible: {has_login}")

        if has_login:
            print("Step 6: Try method B - focus @e23 (password) then Enter")
            await tool({"action": "hover", "ref": "@e23"})
            await asyncio.sleep(0.5)
            await tool({"action": "press", "key": "Enter"})
            await asyncio.sleep(2.0)
            result = await tool({"action": "snapshot"})
            has_login = "密码登录" in (result.output or "")
            print(f"  Login dialog still visible: {has_login}")

        if has_login:
            print("Step 7: Try method C - mouse down/up on @e21")
            # Get center of e21 via eval
            result = await tool({
                "action": "eval",
                "js": """
                    const el = document.querySelector('[data-agent-browser-ref="e21"]') || document.querySelector('[ref="e21"]');
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {x: r.left + r.width/2, y: r.top + r.height/2};
                """
            })
            print(f"  e21 center: {result.output}")
            # Try mouse sequence
            await tool({"action": "mouse", "mouse_action": "move", "x": 640, "y": 450})
            await tool({"action": "mouse", "mouse_action": "down"})
            await asyncio.sleep(0.2)
            await tool({"action": "mouse", "mouse_action": "up"})
            await asyncio.sleep(2.0)
            result = await tool({"action": "snapshot"})
            has_login = "密码登录" in (result.output or "")
            print(f"  Login dialog still visible: {has_login}")

        if has_login:
            print("Step 8: Try method D - click outer cell @e14")
            await tool({"action": "click", "ref": "@e14"})
            await asyncio.sleep(2.0)
            result = await tool({"action": "snapshot"})
            has_login = "密码登录" in (result.output or "")
            print(f"  Login dialog still visible: {has_login}")

        print("\nFinal snapshot:")
        result = await tool({"action": "snapshot"})
        print(result.output[:1000])

        print("\nKeeping browser open for 10s...")
        await asyncio.sleep(10.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
