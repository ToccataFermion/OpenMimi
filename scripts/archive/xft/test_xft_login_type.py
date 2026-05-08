"""Test type action for React custom inputs."""
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

        print("Step 3: Type phone number")
        result = await tool({"action": "type", "ref": "@e22", "value": "18584828398"})
        print(f"  type phone: {result.output}")

        print("Step 4: Type password")
        result = await tool({"action": "type", "ref": "@e23", "value": "Liszt123"})
        print(f"  type password: {result.output}")

        print("Step 5: Check agreement")
        result = await tool({"action": "check", "ref": "@e20"})
        print(f"  check: {result.output}")

        print("Step 6: Click login")
        result = await tool({"action": "click", "ref": "@e21"})
        print(f"  click: {result.output}")
        await asyncio.sleep(3.0)

        print("Step 7: Snapshot")
        result = await tool({"action": "snapshot"})
        print(f"  snapshot len={len(result.output)}")
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
