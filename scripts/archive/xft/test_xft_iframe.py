"""Check if xft login dialog is inside an iframe."""
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
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        print("--- Check for iframes ---")
        result = await tool({
            "action": "eval",
            "js": "return document.querySelectorAll('iframe').length;"
        })
        print(f"iframe count: {result.output}")

        result = await tool({
            "action": "eval",
            "js": """
                return Array.from(document.querySelectorAll('iframe')).map(f => ({
                    src: f.src?.slice(0, 100),
                    id: f.id,
                    name: f.name,
                    width: f.width,
                    height: f.height
                }));
            """
        })
        print(f"iframes: {result.output}")

        print("\n--- Check for shadow DOM ---")
        result = await tool({
            "action": "eval",
            "js": """
                const all = Array.from(document.querySelectorAll('*'));
                const shadowHosts = all.filter(el => el.shadowRoot).map(el => el.tagName);
                return shadowHosts;
            """
        })
        print(f"shadow hosts: {result.output}")

        print("\n--- Query inside iframe if exists ---")
        result = await tool({
            "action": "eval",
            "js": """
                const iframes = document.querySelectorAll('iframe');
                if (iframes.length === 0) return 'no iframes';
                const doc = iframes[0].contentDocument;
                if (!doc) return 'iframe not accessible (cross-origin)';
                const inputs = doc.querySelectorAll('input');
                return {inputCount: inputs.length, firstInput: inputs[0] ? inputs[0].outerHTML?.slice(0, 200) : null};
            """
        })
        print(f"iframe content: {result.output}")

        print("\n--- Check all document body text ---")
        result = await tool({
            "action": "eval",
            "js": "return document.body.innerText?.slice(0, 500);"
        })
        print(f"body text: {result.output}")

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
