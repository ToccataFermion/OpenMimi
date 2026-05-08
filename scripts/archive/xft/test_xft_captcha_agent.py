"""Inspect xft slider CAPTCHA using AgentBrowserTool."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    tool = AgentBrowserTool(download_dir=download_dir, viewport=(1280, 800))

    try:
        log("Step 1: Navigate and open login")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Step 2: Fill credentials")
        await tool({
            "action": "eval",
            "js": """
                (() => {
                    const inputs = Array.from(document.querySelectorAll('input.ant-input'));
                    const phone = inputs.find(el => el.type === 'text');
                    const pass = inputs.find(el => el.type === 'password');
                    const checkbox = document.querySelector('input.ant-checkbox-input');
                    function setReactValue(element, value) {
                        const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        valueSetter.call(element, value);
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    if (phone) setReactValue(phone, '18584828398');
                    if (pass) setReactValue(pass, 'Liszt123');
                    if (checkbox && !checkbox.checked) checkbox.click();
                })()
            """,
        })
        await asyncio.sleep(0.5)

        log("Step 3: Click login to trigger CAPTCHA")
        await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                    if (btn) {
                        btn.focus();
                        ['mousedown', 'mouseup', 'click'].forEach(type => {
                            btn.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                        });
                    }
                })()
            """,
        })
        await asyncio.sleep(2.0)

        log("Step 4: Inspect CAPTCHA elements")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const captchaEls = all.filter(el => {
                        const txt = (el.textContent || '').toLowerCase();
                        const cls = (el.className || '').toLowerCase();
                        return txt.includes('滑块') || txt.includes('拼图') || txt.includes('拖动') ||
                               cls.includes('captcha') || cls.includes('slider') || cls.includes('verify');
                    });
                    return {
                        count: captchaEls.length,
                        elements: captchaEls.slice(0, 10).map(el => {
                            const r = el.getBoundingClientRect();
                            return {
                                tag: el.tagName,
                                class: el.className?.slice(0, 60),
                                id: el.id,
                                text: el.textContent?.trim()?.slice(0, 30),
                                rect: {x: r.x, y: r.y, w: r.width, h: r.height}
                            };
                        })
                    };
                })()
            """,
        })
        log(f"  CAPTCHA elements: {result.output}")

        log("Step 5: Find all images and bg images")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const imgs = Array.from(document.querySelectorAll('img')).map(img => {
                        const r = img.getBoundingClientRect();
                        return {src: img.src?.slice(0, 100), rect: {x: r.x, y: r.y, w: r.width, h: r.height}, class: img.className?.slice(0, 40)};
                    }).filter(i => i.rect.w > 20);
                    const bgDivs = Array.from(document.querySelectorAll('div')).map(div => {
                        const style = window.getComputedStyle(div);
                        const bg = style.backgroundImage;
                        const r = div.getBoundingClientRect();
                        if (bg && bg !== 'none' && r.width > 20) {
                            return {bg: bg?.slice(0, 100), rect: {x: r.x, y: r.y, w: r.width, h: r.height}, class: div.className?.slice(0, 40)};
                        }
                        return null;
                    }).filter(Boolean);
                    return {images: imgs.slice(0, 10), bgDivs: bgDivs.slice(0, 10)};
                })()
            """,
        })
        log(f"  Images: {result.output}")

        log("Step 6: Look for slider handle specifically")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    // Look for elements that might be the handle (small, clickable, near slider text)
                    const handles = all.filter(el => {
                        const r = el.getBoundingClientRect();
                        const parent = el.parentElement;
                        const parentText = parent ? (parent.textContent || '') : '';
                        return r.width > 20 && r.width < 80 && r.height > 20 && r.height < 80 &&
                               (parentText.includes('滑块') || parentText.includes('拖动') ||
                                (el.className || '').toLowerCase().includes('handle') ||
                                (el.className || '').toLowerCase().includes('slider'));
                    });
                    return handles.map(el => {
                        const r = el.getBoundingClientRect();
                        return {tag: el.tagName, class: el.className?.slice(0, 60), id: el.id,
                                rect: {x: r.x, y: r.y, w: r.width, h: r.height}};
                    });
                })()
            """,
        })
        log(f"  Handles: {result.output}")

        log("Step 7: Get full snapshot")
        result = await tool({"action": "snapshot"})
        log(f"  Snapshot length: {len(result.output or '')}")
        log(f"  Snapshot: {result.output}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
