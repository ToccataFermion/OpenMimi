"""Deep inspect CAPTCHA internals to find validation mechanism."""
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
    browser_args = [
        "--disable-blink-features=AutomationControlled",
    ]
    tool = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        browser_args=browser_args,
    )

    try:
        log("Step 1: Navigate and login")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(1.0)
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)
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
                    return {ok: true};
                })()
            """,
        })
        await asyncio.sleep(0.5)
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
                    return {clicked: !!btn};
                })()
            """,
        })
        await asyncio.sleep(3.0)

        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    return {hasButton: !!btn};
                })()
            """,
        })
        if not json.loads(result.output or "{}").get("hasButton"):
            log("  No CAPTCHA, aborting")
            return

        log("Step 2: Inspect React/Vue internals on CAPTCHA elements")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const container = document.querySelector('.imageVerifyDrag');
                    const verify = document.querySelector('.xftImageVerify');

                    function getReactInternals(el) {
                        const keys = Object.keys(el);
                        const reactKey = keys.find(k => k.startsWith('__reactInternalInstance') || k.startsWith('__reactFiber'));
                        if (!reactKey) return null;
                        const fiber = el[reactKey];
                        if (!fiber) return null;
                        // Walk up to find component with state/callbacks
                        let node = fiber;
                        const info = {};
                        for (let i = 0; i < 20 && node; i++) {
                            if (node.memoizedState) {
                                info.state = JSON.stringify(node.memoizedState, (k, v) => {
                                    if (typeof v === 'function') return '[Function]';
                                    return v;
                                }).slice(0, 500);
                            }
                            if (node.memoizedProps) {
                                const props = node.memoizedProps;
                                info.props = Object.keys(props).reduce((acc, k) => {
                                    const v = props[k];
                                    acc[k] = typeof v === 'function' ? '[Function]' : v;
                                    return acc;
                                }, {});
                            }
                            node = node.return;
                        }
                        return info;
                    }

                    return {
                        btnInternals: getReactInternals(btn),
                        dragInternals: drag ? getReactInternals(drag) : null,
                        containerInternals: getReactInternals(container),
                        verifyInternals: verify ? getReactInternals(verify) : null
                    };
                })()
            """,
        })
        log(f"  React internals: {result.output[:3000] if result.output else 'empty'}")

        log("Step 3: Look for verification-related window properties")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const candidates = [];
                    for (const key of Object.keys(window)) {
                        try {
                            const val = window[key];
                            if (typeof val === 'object' && val !== null) {
                                const str = JSON.stringify(val);
                                if (str && (str.includes('verify') || str.includes('captcha') || str.includes('slide'))) {
                                    candidates.push({key, type: typeof val, preview: str.slice(0, 200)});
                                }
                            }
                        } catch (e) {}
                    }
                    return candidates.slice(0, 10);
                })()
            """,
        })
        log(f"  Window props: {result.output[:2000] if result.output else 'empty'}")

        log("Step 4: Inspect script tags for CAPTCHA-related scripts")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const scripts = Array.from(document.querySelectorAll('script'));
                    return scripts.map(s => s.src).filter(src => src && (src.includes('captcha') || src.includes('verify') || src.includes('slide') || src.includes('image'))).slice(0, 10);
                })()
            """,
        })
        log(f"  Scripts: {result.output[:1000] if result.output else 'empty'}")

        log("Step 5: Check for data attributes on CAPTCHA elements")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('[class*="imageVerify"] *, [class*="xftImageVerify"] *'));
                    const withData = all.filter(el => Object.keys(el.dataset).length > 0).map(el => ({
                        tag: el.tagName,
                        class: typeof el.className === 'string' ? el.className.slice(0, 50) : 'n/a',
                        dataset: el.dataset
                    }));
                    return withData.slice(0, 10);
                })()
            """,
        })
        log(f"  Data attributes: {result.output[:2000] if result.output else 'empty'}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
