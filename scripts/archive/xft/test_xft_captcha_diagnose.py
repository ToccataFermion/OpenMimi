"""Diagnose CAPTCHA coordinate systems and element positions."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import tempfile

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.browser import BrowserTool


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_captcha_")
    tool = BrowserTool(download_dir=download_dir, headless=False)

    try:
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(1.0)

        result = await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(3.0)

        if result.details and result.details.get("tab_count", 1) > 1:
            tabs = result.details.get("open_tabs", [])
            popup_idx = None
            for i, t in enumerate(tabs, start=1):
                if "#/index" in (t.get("url") or ""):
                    popup_idx = i
            if popup_idx is not None:
                await tool({"action": "switch_tab", "tab_index": popup_idx})
                await asyncio.sleep(1.0)

        await tool({"action": "click", "target_text": "密码登录"})
        await asyncio.sleep(1.5)

        await tool({"action": "click", "target_text": "我已阅读并同意"})
        await asyncio.sleep(1.0)

        page = await tool._maybe_get_page()
        if page:
            await page.evaluate("""() => {
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                function setNativeValue(el, val) {
                    if (!el) return;
                    nativeSetter.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                const inputs = Array.from(document.querySelectorAll('input')).filter(i => {
                    const r = i.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
                const phone = inputs.find(i => i.type === 'text');
                const pass = inputs.find(i => i.type === 'password');
                const cb = inputs.find(i => i.type === 'checkbox');
                setNativeValue(phone, '18584828398');
                setNativeValue(pass, 'Liszt123');
                if (cb) { cb.checked = true; cb.dispatchEvent(new Event('change', {bubbles: true})); }
            }""")
        await asyncio.sleep(1.0)

        page = await tool._maybe_get_page()
        login_btn = None
        if page:
            login_btn = await page.evaluate("""() => {
                const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                if (!btn) return null;
                const r = btn.getBoundingClientRect();
                return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
            }""")
            if isinstance(login_btn, str):
                try:
                    login_btn = json.loads(login_btn)
                except json.JSONDecodeError:
                    login_btn = None
        if login_btn:
            await tool({"action": "click", "coordinate": [login_btn["x"], login_btn["y"]]})
        await asyncio.sleep(2.0)

        # Check for consent dialog
        page = await tool._maybe_get_page()
        if page:
            dialog_info = await page.evaluate("""() => {
                const allEls = Array.from(document.querySelectorAll('body *'));
                const agreeBtn = allEls.find(b => {
                    const text = (b.innerText || b.textContent || '').trim();
                    return text === '同意' && b.offsetParent !== null;
                });
                if (agreeBtn) {
                    const r = agreeBtn.getBoundingClientRect();
                    return {found: true, text: '同意', x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
                }
                return {found: false};
            }""")
            if isinstance(dialog_info, str):
                try:
                    dialog_info = json.loads(dialog_info)
                except json.JSONDecodeError:
                    dialog_info = {"found": False}
            if dialog_info and dialog_info.get("found"):
                await tool({"action": "click", "coordinate": [dialog_info["x"], dialog_info["y"]]})
                await asyncio.sleep(2.0)

        # CAPTCHA phase - deep inspection
        page = await tool._maybe_get_page()
        if not page:
            print("No page")
            return

        inspect = await page.evaluate("""() => {
            const result = {};
            const captcha = document.querySelector('.xftImageVerify');
            if (captcha) {
                const r = captcha.getBoundingClientRect();
                result.captchaRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
            }
            const bottomImg = document.querySelector('.bottomImage');
            if (bottomImg) {
                const r = bottomImg.getBoundingClientRect();
                result.bottomImageRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
                result.bottomImageNatural = {w: bottomImg.naturalWidth, h: bottomImg.naturalHeight};
                result.bottomImageCurrent = {w: bottomImg.width, h: bottomImg.height};
                result.bottomImageStyle = {
                    width: window.getComputedStyle(bottomImg).width,
                    height: window.getComputedStyle(bottomImg).height,
                    left: window.getComputedStyle(bottomImg).left,
                    top: window.getComputedStyle(bottomImg).top,
                };
            }
            const dragImg = document.querySelector('.dragImage');
            if (dragImg) {
                const r = dragImg.getBoundingClientRect();
                result.dragImageRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
                result.dragImageNatural = {w: dragImg.naturalWidth, h: dragImg.naturalHeight};
                result.dragImageCurrent = {w: dragImg.width, h: dragImg.height};
                result.dragImageStyle = {
                    width: window.getComputedStyle(dragImg).width,
                    height: window.getComputedStyle(dragImg).height,
                    left: window.getComputedStyle(dragImg).left,
                    top: window.getComputedStyle(dragImg).top,
                };
            }
            const verifyImage = document.querySelector('.imageVerifyImage');
            if (verifyImage) {
                const r = verifyImage.getBoundingClientRect();
                result.verifyImageRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
            }
            const dragBtn = document.querySelector('.imageVerifyDragButton');
            if (dragBtn) {
                const r = dragBtn.getBoundingClientRect();
                result.dragButtonRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
            }
            const dragTrack = document.querySelector('.imageVerifyDrag');
            if (dragTrack) {
                const r = dragTrack.getBoundingClientRect();
                result.dragTrackRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
            }
            return JSON.stringify(result);
        }""")
        if isinstance(inspect, str):
            inspect = json.loads(inspect)
        print(json.dumps(inspect, indent=2))

        # Extract images
        bottom_src = await page.evaluate("""() => { const img = document.querySelector('.bottomImage'); return img ? img.src : null; }""")
        drag_src = await page.evaluate("""() => { const img = document.querySelector('.dragImage'); return img ? img.src : null; }""")

        if bottom_src and drag_src:
            def data_url_to_image(src: str) -> Image.Image:
                prefix, b64 = src.split(",", 1)
                data = base64.b64decode(b64)
                return Image.open(io.BytesIO(data))

            bottom_img = data_url_to_image(bottom_src)
            drag_img = data_url_to_image(drag_src)
            print(f"Bottom image natural size: {bottom_img.size}")
            print(f"Drag image natural size: {drag_img.size}")

        print("\nKeeping browser open for 15s...")
        await asyncio.sleep(15.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
