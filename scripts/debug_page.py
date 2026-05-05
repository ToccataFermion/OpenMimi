"""Debug script to inspect the browser_use Page object API."""
import asyncio
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.browser import BrowserTool


async def main():
    download_dir = tempfile.mkdtemp(prefix="openmimi_debug_")
    tool = BrowserTool(download_dir=download_dir, headless=False)

    try:
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(1.0)

        page = await tool._maybe_get_page()
        print(f"Page type: {type(page)}")
        print(f"Page module: {type(page).__module__}")
        print(f"Page class: {type(page).__name__}")

        # Check for common attributes
        attrs = ['page', '_page', 'pw_page', 'playwright_page', 'mouse', 'keyboard', 'touchscreen']
        for attr in attrs:
            if hasattr(page, attr):
                val = getattr(page, attr)
                print(f"page.{attr}: {type(val)} - module: {type(val).__module__}")
                if not asyncio.iscoroutine(val):
                    print(f"  repr: {repr(val)[:200]}")
            else:
                print(f"page.{attr}: NOT FOUND")

        # Check methods
        methods = ['mouse', 'evaluate', 'screenshot', 'click']
        for m in methods:
            if hasattr(page, m):
                val = getattr(page, m)
                print(f"page.{m}: {type(val)}, iscoroutine: {asyncio.iscoroutinefunction(val)}")

        await asyncio.sleep(5.0)
    finally:
        await tool.close()


if __name__ == "__main__":
    asyncio.run(main())
