"""Capture CAPTCHA screenshot for vision analysis."""
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
        log("=== Login ===")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)
        await browser({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)
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
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                    if (btn) { btn.click(); return {clicked: true}; }
                    return {clicked: false};
                })()
            """,
        })
        await asyncio.sleep(4.0)

        # Take screenshot via agent-browser
        screenshot_path = os.path.join(os.path.dirname(__file__), "..", "data", "captcha_screenshot.png")
        result = await browser({
            "action": "screenshot",
            "path": screenshot_path,
        })
        log(f"Screenshot saved: {screenshot_path}")
        log(f"Result: {result.output}")

        # Also get element positions and image sources
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const bg = document.querySelector('.bottomImage');
                    const drag = document.querySelector('.dragImage');
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const track = document.querySelector('.imageVerifyDrag');
                    function getSrc(el) {
                        if (!el) return null;
                        if (el.src) return el.src;
                        const style = window.getComputedStyle(el);
                        const m = style.backgroundImage.match(/url\(["']?(data:image\/[^"']+)["']?\)/);
                        return m ? m[1] : null;
                    }
                    return {
                        hasCaptcha: !!document.querySelector('.xftImageVerify'),
                        bgSrc: getSrc(bg),
                        dragSrc: getSrc(drag),
                        bgRect: bg ? {x: bg.getBoundingClientRect().x, y: bg.getBoundingClientRect().y, w: bg.getBoundingClientRect().width, h: bg.getBoundingClientRect().height} : null,
                        dragRect: drag ? {x: drag.getBoundingClientRect().x, y: drag.getBoundingClientRect().y, w: drag.getBoundingClientRect().width, h: drag.getBoundingClientRect().height} : null,
                        btnRect: btn ? {x: btn.getBoundingClientRect().x, y: btn.getBoundingClientRect().y, w: btn.getBoundingClientRect().width, h: btn.getBoundingClientRect().height} : null,
                        trackRect: track ? {x: track.getBoundingClientRect().x, y: track.getBoundingClientRect().y, w: track.getBoundingClientRect().width, h: track.getBoundingClientRect().height} : null,
                    };
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"DOM data: {json.dumps(data, ensure_ascii=False, indent=2)}")

        # Save images if available
        if data.get("bgSrc"):
            bg_path = os.path.join(os.path.dirname(__file__), "..", "data", "captcha_bg.png")
            with open(bg_path, "wb") as f:
                f.write(base64.b64decode(data["bgSrc"].split(",")[1]))
            log(f"BG image saved: {bg_path}")

        if data.get("dragSrc"):
            drag_path = os.path.join(os.path.dirname(__file__), "..", "data", "captcha_drag.png")
            with open(drag_path, "wb") as f:
                f.write(base64.b64decode(data["dragSrc"].split(",")[1]))
            log(f"Drag image saved: {drag_path}")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
