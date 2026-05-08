"""Inspect CAPTCHA internal state to find gap position or validation logic."""
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
                    if (btn) { btn.click(); return {clicked: true, class: btn.className}; }
                    return {clicked: false};
                })()
            """,
        })
        await asyncio.sleep(4.0)

        log("\n=== Inspect CAPTCHA internals ===")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    // Look for React/Vue component data, data attributes, or exposed state
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const bg = document.querySelector('.bottomImage');
                    const container = document.querySelector('.imageVerify') || document.querySelector('.xftImageVerify');

                    const state = {
                        // Element data attributes
                        btnData: btn ? Object.fromEntries(Array.from(btn.attributes).map(a => [a.name, a.value])) : null,
                        dragData: drag ? Object.fromEntries(Array.from(drag.attributes).map(a => [a.name, a.value])) : null,
                        bgData: bg ? Object.fromEntries(Array.from(bg.attributes).map(a => [a.name, a.value])) : null,

                        // React fiber keys
                        reactKeys: btn ? Object.keys(btn).filter(k => k.startsWith('__react')) : null,

                        // Global variables that might hold CAPTCHA state
                        globalVars: Object.keys(window).filter(k =>
                            /captcha|verify|slider|drag|puzzle|gap/i.test(k) &&
                            typeof window[k] !== 'undefined'
                        ).slice(0, 20),

                        // Check for exposed state on elements
                        btnState: btn ? {
                            offsetLeft: btn.offsetLeft,
                            offsetTop: btn.offsetTop,
                            offsetWidth: btn.offsetWidth,
                            offsetHeight: btn.offsetHeight,
                            clientLeft: btn.clientLeft,
                            clientTop: btn.clientTop,
                        } : null,

                        dragState: drag ? {
                            offsetLeft: drag.offsetLeft,
                            offsetTop: drag.offsetTop,
                            offsetWidth: drag.offsetWidth,
                            offsetHeight: drag.offsetHeight,
                            src: drag.src || null,
                        } : null,

                        bgState: bg ? {
                            offsetLeft: bg.offsetLeft,
                            offsetTop: bg.offsetTop,
                            offsetWidth: bg.offsetWidth,
                            offsetHeight: bg.offsetHeight,
                            src: bg.src || null,
                        } : null,

                        // Container dimensions
                        containerRect: container ? container.getBoundingClientRect() : null,
                    };

                    // Try to find any script tags that might contain CAPTCHA config
                    const scripts = Array.from(document.querySelectorAll('script'));
                    const captchaScripts = scripts.filter(s =>
                        s.src && /captcha|verify|slider|drag/i.test(s.src)
                    ).map(s => s.src);

                    return {...state, captchaScripts};
                })()
            """,
        })
        log(f"  Result: {result.output}")

        log("\n=== Try to find gap in inline scripts ===")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const scripts = Array.from(document.querySelectorAll('script:not([src])'));
                    const matches = [];
                    for (const s of scripts) {
                        const text = s.textContent || '';
                        // Look for coordinates, positions, or gap-related values
                        if (/drag|slider|captcha|verify|gap|position|offset/i.test(text)) {
                            // Extract lines that look like they contain numeric positions
                            const lines = text.split('\\n').filter(l =>
                                /\\d+/.test(l) && (l.includes('left') || l.includes('top') || l.includes('width') || l.includes('position') || l.includes('gap'))
                            ).slice(0, 5);
                            if (lines.length > 0) {
                                matches.push(lines.join('\\n'));
                            }
                        }
                    }
                    return {matches: matches.slice(0, 3)};
                })()
            """,
        })
        log(f"  Inline scripts: {result.output}")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
