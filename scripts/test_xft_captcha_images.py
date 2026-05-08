"""Inspect xft CAPTCHA image sources and try OpenCV gap detection."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool

def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=["--disable-blink-features=AutomationControlled"],
    )

    try:
        log("Navigate to xft")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)

        log("Click login tab")
        await browser({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Fill credentials")
        await browser({
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
                    return {ok: true};
                })()
            """,
        })
        await asyncio.sleep(0.5)

        log("Click login to trigger CAPTCHA")
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                    if (btn) {
                        ['mousedown', 'mouseup', 'click'].forEach(type => {
                            btn.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                        });
                    }
                    return {clicked: !!btn};
                })()
            """,
        })
        await asyncio.sleep(4.0)

        log("Inspect CAPTCHA images")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const bg = document.querySelector('.bottomImage');

                    function imgInfo(el) {
                        if (!el) return null;
                        const tag = el.tagName.toLowerCase();
                        const rect = el.getBoundingClientRect();
                        return {
                            tag,
                            src: el.src || null,
                            style: el.getAttribute('style'),
                            backgroundImage: el.style.backgroundImage || window.getComputedStyle(el).backgroundImage,
                            width: rect.width,
                            height: rect.height,
                            left: rect.left,
                            top: rect.top,
                            className: el.className,
                        };
                    }

                    return {
                        hasButton: !!btn,
                        hasDrag: !!drag,
                        hasBg: !!bg,
                        btnInfo: imgInfo(btn),
                        dragInfo: imgInfo(drag),
                        bgInfo: imgInfo(bg),
                    };
                })()
            """,
        })
        log(f"Image info: {result.output}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
