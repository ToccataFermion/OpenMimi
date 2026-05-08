"""Attempt to bypass xft slider CAPTCHA via DOM manipulation or callback injection."""
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
    tool = AgentBrowserTool(download_dir=download_dir, viewport=(1280, 800))

    try:
        log("Step 1: Navigate and open login")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Step 2: Fill credentials")
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

        log("Step 3: Click login to trigger CAPTCHA")
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
        await asyncio.sleep(2.0)

        log("Step 4: Inspect CAPTCHA internals")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    // Look for global variables related to captcha/verify
                    const globals = Object.keys(window).filter(k => {
                        const lower = k.toLowerCase();
                        return lower.includes('captcha') || lower.includes('verify') || lower.includes('slider') || lower.includes('slide') || lower.includes('imageverify');
                    });

                    // Look for React/Vue/Angular component instances on DOM nodes
                    const all = Array.from(document.querySelectorAll('*'));
                    const captchaEls = all.filter(el => {
                        const keys = Object.keys(el);
                        return keys.some(k => k.toLowerCase().includes('react') || k.toLowerCase().includes('vue') || k.toLowerCase().includes('__vue'));
                    }).slice(0, 5).map(el => {
                        const keys = Object.keys(el).filter(k => k.startsWith('__react') || k.includes('vue'));
                        return {tag: el.tagName, class: el.className?.slice(0, 40), keys};
                    });

                    // Check for event listeners (modern browsers may not expose this easily)
                    const dragImg = document.querySelector('.dragImage');
                    const listeners = dragImg ? {
                        onclick: !!dragImg.onclick,
                        onmousedown: !!dragImg.onmousedown,
                        onmousemove: !!dragImg.onmousemove,
                        onmouseup: !!dragImg.onmouseup,
                    } : null;

                    // Try to find the verify/success callback by looking at script tags
                    const scripts = Array.from(document.querySelectorAll('script')).map(s => s.src).filter(Boolean);

                    return {globals, captchaEls, listeners, scripts: scripts.slice(0, 5)};
                })()
            """,
        })
        log(f"  CAPTCHA internals: {result.output}")

        log("Step 5: Try direct CSS manipulation of dragImage")
        # Try setting the left position directly and dispatching a custom event
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const drag = document.querySelector('.dragImage');
                    const bottom = document.querySelector('.bottomImage');
                    if (!drag || !bottom) return {error: 'elements not found'};

                    // Try different left positions
                    const positions = [100, 150, 200, 250];
                    const results = [];

                    for (const pos of positions) {
                        drag.style.left = pos + 'px';
                        drag.style.position = 'absolute';
                        drag.dispatchEvent(new Event('change', {bubbles: true}));
                        drag.dispatchEvent(new Event('input', {bubbles: true}));
                        drag.dispatchEvent(new CustomEvent('verify', {bubbles: true}));
                        results.push({pos, rect: drag.getBoundingClientRect().x});
                    }

                    return {results, dragClass: drag.className, parentClass: drag.parentElement?.className?.slice(0, 40)};
                })()
            """,
        })
        log(f"  CSS manipulation: {result.output}")

        log("Step 6: Inspect parent containers for verification state")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const containers = all.filter(el => {
                        const cls = typeof el.className === 'string' ? el.className : '';
                        return cls.includes('Verify') || cls.includes('verify') || cls.includes('Captcha') || cls.includes('captcha');
                    }).map(el => {
                        const style = window.getComputedStyle(el);
                        return {
                            tag: el.tagName,
                            class: el.className?.slice(0, 60),
                            display: style.display,
                            visibility: style.visibility,
                            opacity: style.opacity,
                        };
                    });
                    return containers;
                })()
            """,
        })
        log(f"  Containers: {result.output}")

        log("Step 7: Try to find and call verification function via prototype")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    // Look for functions that might be the verify callback
                    const candidates = [];
                    for (const key of Object.keys(window)) {
                        try {
                            const val = window[key];
                            if (typeof val === 'function') {
                                const src = val.toString();
                                if (src.includes('verify') || src.includes('captcha') || src.includes('slide') || src.includes('success')) {
                                    candidates.push(key);
                                }
                            }
                        } catch (e) {}
                    }
                    return candidates.slice(0, 20);
                })()
            """,
        })
        log(f"  Function candidates: {result.output}")

        log("Step 8: Check if there's a form or hidden input that stores the verification token")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const inputs = Array.from(document.querySelectorAll('input[type="hidden"]'));
                    return inputs.map(el => ({
                        name: el.name,
                        id: el.id,
                        value: el.value?.slice(0, 50),
                        class: el.className?.slice(0, 40)
                    }));
                })()
            """,
        })
        log(f"  Hidden inputs: {result.output}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
