"""Deep inspect CAPTCHA JavaScript for target position."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.browser import BrowserTool


_DEEP_INSPECT_JS = """() => {
    const result = {
        reactInternals: null,
        captchaState: null,
        windowVars: [],
        localStorage: {},
        sessionStorage: {}
    };

    // Try React DevTools hook
    if (window.__REACT_DEVTOOLS_GLOBAL_HOOK__) {
        result.reactInternals = 'hook present';
    }

    // Try to find React fibers on CAPTCHA elements
    const captchaEl = document.querySelector('.xftImageVerify');
    if (captchaEl) {
        const keys = Object.keys(captchaEl);
        for (const key of keys) {
            if (key.startsWith('__reactInternalInstance') || key.startsWith('__reactFiber')) {
                const fiber = captchaEl[key];
                result.reactInternals = {key: key, type: fiber?.type?.name || fiber?.type || 'unknown'};
                // Walk up the fiber tree looking for state
                let current = fiber;
                let depth = 0;
                while (current && depth < 20) {
                    if (current.memoizedState) {
                        const state = current.memoizedState;
                        if (typeof state === 'object' && state !== null) {
                            const stateKeys = Object.keys(state);
                            const numericValues = {};
                            for (const k of stateKeys) {
                                const v = state[k];
                                if (typeof v === 'number' && v > 0 && v < 400) {
                                    numericValues[k] = v;
                                } else if (typeof v === 'object' && v !== null) {
                                    for (const subK of Object.keys(v)) {
                                        const subV = v[subK];
                                        if (typeof subV === 'number' && subV > 0 && subV < 400) {
                                            numericValues[k + '.' + subK] = subV;
                                        }
                                    }
                                }
                            }
                            if (Object.keys(numericValues).length > 0) {
                                result.captchaState = result.captchaState || [];
                                result.captchaState.push({
                                    depth: depth,
                                    component: current.type?.name || current.type || 'unknown',
                                    values: numericValues
                                });
                            }
                        }
                    }
                    current = current.return;
                    depth++;
                }
                break;
            }
        }
    }

    // Search window for any object with a property matching common CAPTCHA field names
    const searchTerms = ['offset', 'gap', 'target', 'verify', 'slider', 'captcha', 'x', 'y', 'width', 'answer'];
    for (const key of Object.keys(window)) {
        try {
            const val = window[key];
            if (val && typeof val === 'object' && !Array.isArray(val)) {
                const keys = Object.keys(val);
                const matches = keys.filter(k => searchTerms.some(t => k.toLowerCase().includes(t)));
                if (matches.length > 0) {
                    const filtered = {};
                    for (const k of matches) {
                        const v = val[k];
                        if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') {
                            filtered[k] = v;
                        }
                    }
                    if (Object.keys(filtered).length > 0) {
                        result.windowVars.push({name: key, matches: filtered});
                    }
                }
            }
        } catch (e) {}
    }

    // Check storage
    try {
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            result.localStorage[k] = localStorage.getItem(k);
        }
    } catch (e) {}
    try {
        for (let i = 0; i < sessionStorage.length; i++) {
            const k = sessionStorage.key(i);
            result.sessionStorage[k] = sessionStorage.getItem(k);
        }
    } catch (e) {}

    return JSON.stringify(result);
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

        inspect_raw = await page.evaluate(_DEEP_INSPECT_JS)
        if isinstance(inspect_raw, str):
            inspect = json.loads(inspect_raw)
        else:
            inspect = inspect_raw

        print("React internals:", inspect.get("reactInternals"))
        print("\nCaptcha state from React:")
        for state in inspect.get("captchaState", []):
            print(f"  depth={state['depth']} component={state['component']}: {state['values']}")

        print("\nWindow vars matching search terms:")
        for obj in inspect.get("windowVars", []):
            print(f"  {obj['name']}: {obj['matches']}")

        print("\nLocal storage:", inspect.get("localStorage"))
        print("\nSession storage:", inspect.get("sessionStorage"))

        print("\nKeeping browser open for 15s...")
        await asyncio.sleep(15.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
