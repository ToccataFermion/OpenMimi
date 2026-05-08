"""Find CAPTCHA implementation in page scripts."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

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

        # CAPTCHA phase - get all script sources
        page = await tool._maybe_get_page()
        if not page:
            print("No page")
            return

        scripts = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('script[src]')).map(s => s.src).filter(src => src.includes('.js'));
        }""")
        print(f"Found {len(scripts)} script sources:")
        for src in scripts[:20]:
            print(f"  {src}")

        # Search page source for CAPTCHA-related strings
        source_search = await page.evaluate("""() => {
            const html = document.documentElement.innerHTML;
            const matches = [];
            const terms = ['xftImageVerify', 'imageVerify', 'bottomImage', 'dragImage', 'imageVerifyDrag'];
            for (const term of terms) {
                const idx = html.indexOf(term);
                if (idx >= 0) {
                    matches.push({term, context: html.slice(Math.max(0, idx-50), idx+100)});
                }
            }
            return JSON.stringify(matches);
        }""")
        if isinstance(source_search, str):
            source_search = json.loads(source_search)
        print(f"\nSource matches: {json.dumps(source_search, indent=2)[:3000]}")

        # Try to find any fetch/XHR that might return CAPTCHA data
        network_data = await page.evaluate("""() => {
            // Check if there are any global fetch interceptors or cached responses
            const result = {fetchCache: [], xhrCache: []};

            // Look for any objects that might contain CAPTCHA config
            for (const key of Object.keys(window)) {
                try {
                    const val = window[key];
                    if (val && typeof val === 'object') {
                        const json = JSON.stringify(val);
                        if (json.includes('captcha') || json.includes('verify') || json.includes('imageVerify')) {
                            result.fetchCache.push({key, snippet: json.slice(0, 200)});
                        }
                    }
                } catch (e) {}
            }
            return JSON.stringify(result);
        }""")
        if isinstance(network_data, str):
            network_data = json.loads(network_data)
        print(f"\nNetwork/cache data: {json.dumps(network_data, indent=2)[:3000]}")

        print("\nKeeping browser open for 15s...")
        await asyncio.sleep(15.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
