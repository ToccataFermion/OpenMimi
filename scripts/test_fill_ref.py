"""Test fill action with and without @ prefix."""
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
        # Navigate and open login dialog
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(1.5)

        # Snapshot to get refs
        result = await tool({"action": "snapshot"})
        print(f"Snapshot done. Output len: {len(result.output)}")

        # Try fill without @
        print("\n--- Trying fill e22 ---")
        result = await tool({"action": "fill", "ref": "e22", "value": "test1"})
        print(f"fill e22: output={result.output}, error={result.is_error}")

        # Try fill with @
        print("\n--- Trying fill @e22 ---")
        result = await tool({"action": "fill", "ref": "@e22", "value": "test2"})
        print(f"fill @e22: output={result.output}, error={result.is_error}")

        # Try type with @
        print("\n--- Trying type @e22 ---")
        result = await tool({"action": "type", "ref": "@e22", "value": "test3"})
        print(f"type @e22: output={result.output}, error={result.is_error}")

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
