"""Clean xft login test."""
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
        result = await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        print(f"  OK: {result.output[:100]}")

        print("Step 2: Click login")
        result = await tool({"action": "click", "target_text": "登录"})
        print(f"  OK: {result.output}")
        await asyncio.sleep(2.0)

        print("Step 3: Snapshot login dialog")
        result = await tool({"action": "snapshot"})
        print(f"  OK: snapshot length={len(result.output)}")
        refs = result.details.get("refs", {})
        print(f"  refs count={len(refs)}")
        # Find phone/password refs by role
        for ref_id, info in list(refs.items())[:10]:
            print(f"    {ref_id}: {info}")

        print("Step 4: Fill phone (@e22)")
        result = await tool({"action": "fill", "ref": "@e22", "value": "18584828398"})
        print(f"  OK: {result.output}")

        print("Step 5: Fill password (@e23)")
        result = await tool({"action": "fill", "ref": "@e23", "value": "Liszt123"})
        print(f"  OK: {result.output}")

        print("Step 6: Check agreement (@e20)")
        result = await tool({"action": "check", "ref": "@e20"})
        print(f"  OK: {result.output}")

        print("Step 7: Click login button (@e21)")
        result = await tool({"action": "click", "ref": "@e21"})
        print(f"  OK: {result.output}")
        await asyncio.sleep(2.0)

        print("Step 8: Wait 5s then snapshot")
        await tool({"action": "wait", "milliseconds": 5000})
        result = await tool({"action": "snapshot"})
        print(f"  OK: snapshot length={len(result.output)}")
        print(f"  First 500 chars: {result.output[:500]}")

        print("\nDone. Keeping browser open for 10s...")
        await asyncio.sleep(10.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
