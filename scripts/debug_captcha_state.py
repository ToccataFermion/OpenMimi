"""Inspect CAPTCHA internal state via JavaScript."""
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

        # Try to inspect React/Vue component state
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const results = {};

                    // Check for React internal props
                    const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                    if (verify) {
                        const keys = Object.keys(verify);
                        const reactKey = keys.find(k => k.startsWith('__reactInternalInstance') || k.startsWith('__reactFiber'));
                        const vueKey = keys.find(k => k.startsWith('__vue'));
                        results.hasReact = !!reactKey;
                        results.hasVue = !!vueKey;

                        if (reactKey) {
                            const fiber = verify[reactKey];
                            // Walk up to find state
                            let node = fiber;
                            for (let i = 0; i < 20 && node; i++) {
                                if (node.memoizedState || node.stateNode) {
                                    results[`fiber_${i}`] = {
                                        hasState: !!node.memoizedState,
                                        stateKeys: node.memoizedState ? Object.keys(node.memoizedState).slice(0, 10) : null,
                                        stateNodeType: node.stateNode ? node.stateNode.constructor?.name : null,
                                    };
                                }
                                node = node.return;
                            }
                        }
                    }

                    // Check global variables
                    results.globalKeys = Object.keys(window).filter(k =>
                        k.toLowerCase().includes('captcha') ||
                        k.toLowerCase().includes('verify') ||
                        k.toLowerCase().includes('slider')
                    ).slice(0, 20);

                    // Check if there are any data attributes
                    const btn = document.querySelector('.imageVerifyDragButton');
                    if (btn) {
                        results.btnDataset = btn.dataset;
                        results.btnAttributes = Array.from(btn.attributes).map(a => ({name: a.name, value: a.value.substring(0, 50)}));
                    }

                    const bg = document.querySelector('.bottomImage');
                    if (bg) {
                        results.bgDataset = bg.dataset;
                    }

                    return results;
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"State inspection: {json.dumps(data, ensure_ascii=False, indent=2)}")

        # Try to find any script tags with CAPTCHA data
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const scripts = Array.from(document.querySelectorAll('script'));
                    const captchaScripts = scripts.filter(s =>
                        s.textContent && (
                            s.textContent.includes('captcha') ||
                            s.textContent.includes('verify') ||
                            s.textContent.includes('slider') ||
                            s.textContent.includes('gap')
                        )
                    );
                    return {
                        totalScripts: scripts.length,
                        captchaScripts: captchaScripts.length,
                        snippets: captchaScripts.map(s => s.textContent.substring(0, 500)).slice(0, 3)
                    };
                })()
            """,
        })
        data2 = json.loads(result.output or "{}")
        log(f"Scripts: {json.dumps(data2, ensure_ascii=False, indent=2)}")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
