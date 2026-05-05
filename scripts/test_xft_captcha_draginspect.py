"""Drag the slider and inspect DOM/network changes."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.browser import BrowserTool


async def cdp_drag(page, start_x: int, start_y: int, distance: int, steps: int = 40) -> None:
    client = page._client
    session_id = page._session_id

    await client.send.Input.dispatchMouseEvent(
        {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1, "pointerType": "mouse"},
        session_id=session_id,
    )

    for i in range(1, steps + 1):
        t = i / steps
        # Sine ease in-out for more human-like movement
        ease = 0.5 * (1 - math.cos(t * math.pi))
        cx = start_x + int(distance * ease)
        # More random y variation
        cy = start_y + int(3 * math.sin(i * 0.7))
        await client.send.Input.dispatchMouseEvent(
            {"type": "mouseMoved", "x": cx, "y": cy, "button": "left", "buttons": 1, "pointerType": "mouse"},
            session_id=session_id,
        )
        # Variable delay
        await asyncio.sleep(0.02 + 0.015 * math.sin(i * 0.3))

    end_x = start_x + distance
    await client.send.Input.dispatchMouseEvent(
        {"type": "mouseReleased", "x": end_x, "y": start_y, "button": "left", "clickCount": 1, "pointerType": "mouse"},
        session_id=session_id,
    )


async def get_captcha_state(page) -> dict:
    raw = await page.evaluate("""() => {
        const result = {};
        const selectors = ['.xftImageVerify', '.imageVerify', '.imageVerifyImage', '.imageVerifyDrag',
                          '.imageVerifyDragButton', '.imageVerifyDragProgressbar', '.bottomImage', '.dragImage',
                          '.imageRefresh', '.imageVerifyText'];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el) {
                const r = el.getBoundingClientRect();
                result[sel] = {
                    rect: {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)},
                    className: el.className,
                    style: {
                        left: el.style.left,
                        top: el.style.top,
                        width: el.style.width,
                        height: el.style.height,
                        transform: el.style.transform,
                        opacity: el.style.opacity,
                        display: el.style.display,
                    },
                    text: (el.innerText || '').trim().slice(0, 50)
                };
            }
        }
        return JSON.stringify(result);
    }""")
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


async def main() -> None:
    import math

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

        # CAPTCHA phase
        page = await tool._maybe_get_page()
        if not page:
            print("No page")
            return

        print("=== BEFORE DRAG ===")
        before_state = await get_captcha_state(page)
        print(json.dumps(before_state, indent=2))

        # Try dragging to different positions
        # First, try a small drag to see if anything changes
        print("\n=== DRAG 20px ===")
        await cdp_drag(page, 500, 551, 20, steps=10)
        await asyncio.sleep(1.5)
        state_20 = await get_captcha_state(page)
        print(json.dumps(state_20, indent=2))

        # Then drag more
        print("\n=== DRAG additional 80px (total 100px) ===")
        await cdp_drag(page, 520, 551, 80, steps=20)
        await asyncio.sleep(1.5)
        state_100 = await get_captcha_state(page)
        print(json.dumps(state_100, indent=2))

        # Then drag to 200px total
        print("\n=== DRAG additional 100px (total 200px) ===")
        await cdp_drag(page, 600, 551, 100, steps=30)
        await asyncio.sleep(2.0)
        state_200 = await get_captcha_state(page)
        print(json.dumps(state_200, indent=2))

        # Check for any result text
        result = await tool({"action": "extract", "instruction": "get text"})
        text = result.output
        print(f"\nPage text: {text[:500]}")

        print("\nKeeping browser open for 15s...")
        await asyncio.sleep(15.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
