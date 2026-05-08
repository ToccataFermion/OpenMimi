"""Inspect CAPTCHA JavaScript for internal state and target position."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.browser import BrowserTool


_INSPECT_JS = """() => {
    const result = {
        globalKeys: Object.keys(window).filter(k => {
            try {
                const val = window[k];
                return val && typeof val === 'object' && Object.keys(val).length > 0;
            } catch(e) { return false; }
        }),
        suspiciousObjects: [],
        eventListeners: {},
        captchaElements: {}
    };

    // Search for objects with numeric properties in captcha-related ranges
    for (const key of Object.keys(window)) {
        try {
            const val = window[key];
            if (val && typeof val === 'object') {
                const keys = Object.keys(val);
                const numericKeys = keys.filter(k => {
                    try {
                        const v = val[k];
                        return typeof v === 'number' && v > 0 && v < 500;
                    } catch(e) { return false; }
                });
                if (numericKeys.length >= 2) {
                    const filtered = {};
                    for (const k of numericKeys) {
                        filtered[k] = val[k];
                    }
                    result.suspiciousObjects.push({name: key, values: filtered});
                }
            }
        } catch (e) {}
    }

    // Check specific CAPTCHA element styles and transforms
    const els = ['.xftImageVerify', '.imageVerify', '.imageVerifyImage', '.imageVerifyDrag',
                 '.imageVerifyDragButton', '.imageVerifyDragProgressbar', '.bottomImage', '.dragImage'];
    for (const sel of els) {
        const el = document.querySelector(sel);
        if (el) {
            const r = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            result.captchaElements[sel] = {
                rect: {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)},
                style: {
                    left: style.left,
                    top: style.top,
                    transform: style.transform,
                    width: style.width,
                    height: style.height,
                    position: style.position,
                    opacity: style.opacity,
                    zIndex: style.zIndex
                }
            };
        }
    }

    // Try to find any exposed verification functions
    const funcNames = ['verify', 'check', 'validate', 'submit', 'success', 'fail'];
    for (const key of Object.keys(window)) {
        try {
            if (typeof window[key] === 'function') {
                const name = window[key].name || '';
                if (funcNames.some(f => name.toLowerCase().includes(f))) {
                    result.suspiciousObjects.push({name: key, isFunction: true, funcName: name});
                }
            }
        } catch (e) {}
    }

    return JSON.stringify(result);
}"""


_DRAG_EVENT_LOG_JS = """() => {
    const btn = document.querySelector('.imageVerifyDragButton');
    const track = document.querySelector('.imageVerifyDrag');
    if (!btn || !track) return JSON.stringify({error: 'missing elements'});

    const logs = [];
    const events = ['mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend',
                    'pointerdown', 'pointermove', 'pointerup', 'dragstart', 'drag', 'dragend'];

    for (const ev of events) {
        btn.addEventListener(ev, (e) => {
            logs.push({
                type: ev,
                target: e.target.className || e.target.tagName,
                clientX: e.clientX,
                clientY: e.clientY,
                buttons: e.buttons,
                isTrusted: e.isTrusted,
                time: Date.now()
            });
        }, {capture: true});
        track.addEventListener(ev, (e) => {
            logs.push({
                type: ev,
                target: e.target.className || e.target.tagName,
                clientX: e.clientX,
                clientY: e.clientY,
                buttons: e.buttons,
                isTrusted: e.isTrusted,
                time: Date.now()
            });
        }, {capture: true});
    }

    window._captchaEventLogs = logs;
    return JSON.stringify({status: 'listeners attached'});
}"""


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

        # CAPTCHA phase
        page = await tool._maybe_get_page()
        if not page:
            print("No page")
            return

        # Deep inspect
        inspect_raw = await page.evaluate(_INSPECT_JS)
        if isinstance(inspect_raw, str):
            inspect = json.loads(inspect_raw)
        else:
            inspect = inspect_raw
        print("Global keys (first 50):", inspect.get("globalKeys", [])[:50])
        print("\nSuspicious objects:")
        for obj in inspect.get("suspiciousObjects", []):
            print(f"  {obj}")
        print("\nCaptcha elements:")
        for sel, info in inspect.get("captchaElements", {}).items():
            print(f"  {sel}: {json.dumps(info, indent=2)}")

        # Attach event loggers
        log_result = await page.evaluate(_DRAG_EVENT_LOG_JS)
        print(f"\nEvent logger: {log_result}")

        # Now perform a small test drag and check what events were logged
        client = page._client
        session_id = page._session_id
        await client.send.Input.dispatchMouseEvent(
            {"type": "mousePressed", "x": 500, "y": 551, "button": "left", "clickCount": 1, "pointerType": "mouse"},
            session_id=session_id,
        )
        await asyncio.sleep(0.1)
        await client.send.Input.dispatchMouseEvent(
            {"type": "mouseMoved", "x": 520, "y": 551, "button": "left", "buttons": 1, "pointerType": "mouse"},
            session_id=session_id,
        )
        await asyncio.sleep(0.1)
        await client.send.Input.dispatchMouseEvent(
            {"type": "mouseReleased", "x": 520, "y": 551, "button": "left", "clickCount": 1, "pointerType": "mouse"},
            session_id=session_id,
        )
        await asyncio.sleep(1.0)

        # Get event logs
        logs_raw = await page.evaluate("""() => {
            return JSON.stringify(window._captchaEventLogs || []);
        }""")
        if isinstance(logs_raw, str):
            logs = json.loads(logs_raw)
        else:
            logs = logs_raw
        print(f"\nEvent logs ({len(logs)} events):")
        for log in logs:
            print(f"  {log}")

        # Also get final element positions
        pos_raw = await page.evaluate("""() => {
            const dragImg = document.querySelector('.dragImage');
            const btn = document.querySelector('.imageVerifyDragButton');
            const dragR = dragImg ? dragImg.getBoundingClientRect() : null;
            const btnR = btn ? btn.getBoundingClientRect() : null;
            return JSON.stringify({
                dragLeft: dragImg ? dragImg.style.left : null,
                btnLeft: btn ? btn.style.left : null,
                dragX: dragR ? Math.round(dragR.left) : null,
                btnX: btnR ? Math.round(btnR.left) : null
            });
        }""")
        if isinstance(pos_raw, str):
            pos = json.loads(pos_raw)
        else:
            pos = pos_raw
        print(f"\nFinal positions: {pos}")

        print("\nKeeping browser open for 15s...")
        await asyncio.sleep(15.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
