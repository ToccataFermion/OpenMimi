"""Smoke test for daemon warmup."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    t0 = time.monotonic()
    tool = AgentBrowserTool(download_dir=download_dir, viewport=(1280, 800))
    init_dt = time.monotonic() - t0
    print(f"Tool init took {init_dt:.1f}s (warmup thread started)")

    t0 = time.monotonic()
    result = await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
    nav_dt = time.monotonic() - t0
    print(f"Navigate took {nav_dt:.1f}s, error={result.is_error}")
    print(result.output[:500])

    await tool.close()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
