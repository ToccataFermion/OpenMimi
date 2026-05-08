"""Deep inspection of CAPTCHA-related JavaScript."""
from __future__ import annotations

import asyncio
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

        # Deep JS inspection
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const results = {};

                    // 1. Search window object for CAPTCHA-related data
                    const captchaKeys = [];
                    for (const key in window) {
                        try {
                            const val = window[key];
                            if (typeof val === 'number' && val > 50 && val < 300) {
                                captchaKeys.push({key, val, type: 'number'});
                            } else if (typeof val === 'object' && val !== null) {
                                const str = JSON.stringify(val);
                                if (str && str.includes('verify') || str.includes('captcha') || str.includes('slider')) {
                                    captchaKeys.push({key, type: 'object', preview: str.substring(0, 100)});
                                }
                            }
                        } catch (e) {}
                    }
                    results.windowNumbers = captchaKeys.slice(0, 30);

                    // 2. Look for functions that might validate
                    const funcs = [];
                    for (const key in window) {
                        try {
                            if (typeof window[key] === 'function') {
                                const src = window[key].toString();
                                if (src.includes('drag') || src.includes('verify') || src.includes('captcha') || src.includes('slider') || src.includes('gap')) {
                                    funcs.push({key, src: src.substring(0, 200)});
                                }
                            }
                        } catch (e) {}
                    }
                    results.functions = funcs.slice(0, 10);

                    // 3. Hook into fetch/XHR to capture CAPTCHA-related requests
                    // (Can't retroactively hook, but we can check if there are stored responses)

                    // 4. Look at all img src and data attributes
                    const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                    if (verify) {
                        const imgs = verify.querySelectorAll('img');
                        results.imageSrcs = Array.from(imgs).map(img => ({
                            class: img.className,
                            src: img.src ? img.src.substring(0, 100) : null,
                            naturalWidth: img.naturalWidth,
                            naturalHeight: img.naturalHeight,
                        }));
                    }

                    // 5. Check for any data in localStorage/sessionStorage
                    results.localStorage = {};
                    for (let i = 0; i < localStorage.length; i++) {
                        const k = localStorage.key(i);
                        if (k && (k.includes('captcha') || k.includes('verify'))) {
                            results.localStorage[k] = localStorage.getItem(k)?.substring(0, 100);
                        }
                    }
                    results.sessionStorage = {};
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const k = sessionStorage.key(i);
                        if (k && (k.includes('captcha') || k.includes('verify'))) {
                            results.sessionStorage[k] = sessionStorage.getItem(k)?.substring(0, 100);
                        }
                    }

                    return results;
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"JS inspection: {json.dumps(data, ensure_ascii=False, indent=2)}")

        # Try to extract React state more deeply
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                    if (!verify) return {error: 'no verify'};

                    const keys = Object.keys(verify);
                    const reactKey = keys.find(k => k.startsWith('__reactInternalInstance') || k.startsWith('__reactFiber'));
                    if (!reactKey) return {error: 'no react'};

                    const fiber = verify[reactKey];
                    let node = fiber;
                    const stateChain = [];

                    for (let i = 0; i < 30 && node; i++) {
                        const entry = {depth: i, type: node.type};

                        if (node.memoizedState) {
                            try {
                                const state = node.memoizedState;
                                entry.stateType = typeof state;
                                if (typeof state === 'object' && state !== null) {
                                    entry.stateKeys = Object.keys(state).slice(0, 20);
                                    // Try to extract numeric values that could be positions
                                    entry.numericValues = Object.entries(state)
                                        .filter(([k, v]) => typeof v === 'number')
                                        .map(([k, v]) => ({key: k, value: v}))
                                        .slice(0, 10);
                                }
                            } catch (e) {}
                        }

                        if (node.memoizedProps) {
                            try {
                                const props = node.memoizedProps;
                                entry.propKeys = Object.keys(props).slice(0, 20);
                                entry.numericProps = Object.entries(props)
                                    .filter(([k, v]) => typeof v === 'number')
                                    .map(([k, v]) => ({key: k, value: v}))
                                    .slice(0, 10);
                            } catch (e) {}
                        }

                        stateChain.push(entry);
                        node = node.return;
                    }

                    return {chainLength: stateChain.length, chain: stateChain};
                })()
            """,
        })
        data2 = json.loads(result.output or "{}")
        log(f"React state: {json.dumps(data2, ensure_ascii=False, indent=2)}")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
