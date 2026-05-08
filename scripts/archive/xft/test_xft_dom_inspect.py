"""Inspect DOM structure of xft login form."""
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
        print("Navigate and open login dialog")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)
        await tool({"action": "snapshot"})

        print("\n--- Query all elements with placeholder ---")
        result = await tool({
            "action": "eval",
            "js": """
                return Array.from(document.querySelectorAll('*'))
                    .filter(el => el.getAttribute('placeholder'))
                    .map(el => ({
                        tag: el.tagName,
                        placeholder: el.getAttribute('placeholder'),
                        class: el.className?.slice(0, 50),
                        type: el.type,
                        role: el.getAttribute('role')
                    }));
            """
        })
        print(result.output)

        print("\n--- Query all contenteditable elements ---")
        result = await tool({
            "action": "eval",
            "js": """
                return Array.from(document.querySelectorAll('[contenteditable="true"]'))
                    .map(el => ({
                        tag: el.tagName,
                        class: el.className?.slice(0, 50),
                        text: el.textContent?.slice(0, 30)
                    }));
            """
        })
        print(result.output)

        print("\n--- Query elements near '登录' text ---")
        result = await tool({
            "action": "eval",
            "js": """
                const all = Array.from(document.querySelectorAll('*'));
                const loginBtn = all.find(el => el.textContent.trim() === '登录');
                if (!loginBtn) return null;
                const parent = loginBtn.parentElement;
                const siblings = Array.from(parent.children).map(el => ({
                    tag: el.tagName,
                    class: el.className?.slice(0, 50),
                    text: el.textContent?.slice(0, 30)
                }));
                return {loginTag: loginBtn.tagName, loginClass: loginBtn.className?.slice(0, 50), parentTag: parent.tagName, siblings: siblings};
            """
        })
        print(result.output)

        print("\n--- Find elements with React internal keys ---")
        result = await tool({
            "action": "eval",
            "js": """
                const all = Array.from(document.querySelectorAll('*'));
                const reactEls = all.filter(el => {
                    const keys = Object.keys(el);
                    return keys.some(k => k.startsWith('__react'));
                }).slice(0, 10);
                return reactEls.map(el => {
                    const key = Object.keys(el).find(k => k.startsWith('__reactProps'));
                    const props = key ? el[key] : {};
                    return {
                        tag: el.tagName,
                        class: el.className?.slice(0, 50),
                        hasOnChange: !!props.onChange,
                        hasOnClick: !!props.onClick,
                        hasValue: 'value' in props
                    };
                });
            """
        })
        print(result.output)

        print("\n--- Dump HTML of login form area ---")
        result = await tool({
            "action": "eval",
            "js": """
                const all = Array.from(document.querySelectorAll('*'));
                const loginBtn = all.find(el => el.textContent.trim() === '登录');
                if (!loginBtn) return null;
                let container = loginBtn;
                for (let i = 0; i < 5; i++) {
                    if (!container.parentElement) break;
                    container = container.parentElement;
                }
                return container.outerHTML?.slice(0, 3000);
            """
        })
        print(result.output)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
