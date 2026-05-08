"""Inspect xft CAPTCHA JS to understand validation logic."""
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
    browser_args = ["--disable-blink-features=AutomationControlled"]
    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=browser_args,
    )

    try:
        log("Navigate to xft")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)

        log("Click login tab")
        await browser({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Fill credentials")
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

        log("Click login to trigger CAPTCHA")
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                    if (btn) {
                        ['mousedown', 'mouseup', 'click'].forEach(type => {
                            btn.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                        });
                    }
                    return {clicked: !!btn};
                })()
            """,
        })
        await asyncio.sleep(4.0)

        log("Inspect CAPTCHA elements and listeners")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const bg = document.querySelector('.bottomImage');
                    const modal = document.querySelector('.verifyModal') || document.querySelector('.imageVerifyModal');

                    // Check event listeners on the button
                    const btnListeners = [];
                    try {
                        // getEventListeners is available in DevTools but not in page context
                        // Try to monkey-patch addEventListener to capture listeners
                    } catch(e) {}

                    // Check for React/Vue component props
                    const btnKeys = btn ? Object.keys(btn) : [];
                    const reactKey = btnKeys.find(k => k.startsWith('__reactProps') || k.startsWith('__reactEventHandlers'));
                    const reactProps = reactKey ? btn[reactKey] : null;

                    // Look for data attributes
                    const dataAttrs = {};
                    if (btn) {
                        for (const attr of btn.attributes) {
                            if (attr.name.startsWith('data-')) {
                                dataAttrs[attr.name] = attr.value;
                            }
                        }
                    }

                    // Check parent component for validation logic
                    let parentInfo = null;
                    let el = btn;
                    for (let i = 0; i < 5 && el; i++) {
                        el = el.parentElement;
                        if (el && (el.className || '').includes('verify')) {
                            parentInfo = {
                                className: el.className,
                                id: el.id,
                                childCount: el.children.length,
                            };
                            break;
                        }
                    }

                    // Look for global CAPTCHA objects
                    const globalKeys = Object.keys(window).filter(k =>
                        k.toLowerCase().includes('captcha') ||
                        k.toLowerCase().includes('verify') ||
                        k.toLowerCase().includes('slider')
                    );

                    return {
                        hasButton: !!btn,
                        hasBg: !!bg,
                        hasModal: !!modal,
                        btnClass: btn ? btn.className : null,
                        btnStyle: btn ? btn.getAttribute('style') : null,
                        dataAttrs,
                        reactPropsKeys: reactProps ? Object.keys(reactProps).slice(0, 20) : null,
                        parentInfo,
                        globalKeys: globalKeys.slice(0, 20),
                    };
                })()
            """,
        })
        log(f"Element inspection: {result.output}")

        log("Try to hook into mouse events on the button")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    if (!btn) return {error: 'no button'};

                    let eventLog = [];
                    ['mousedown', 'mousemove', 'mouseup', 'pointerdown', 'pointermove', 'pointerup', 'touchstart', 'touchmove', 'touchend'].forEach(type => {
                        btn.addEventListener(type, e => {
                            eventLog.push({
                                type: e.type,
                                isTrusted: e.isTrusted,
                                pointerType: e.pointerType || null,
                                buttons: e.buttons,
                                clientX: e.clientX,
                                clientY: e.clientY,
                                time: Date.now(),
                            });
                            if (eventLog.length > 50) eventLog.shift();
                        }, true);
                    });

                    // Also monitor document for drag-related events
                    document.addEventListener('mousemove', e => {
                        if (e.target === btn || btn.contains(e.target)) {
                            eventLog.push({
                                type: 'doc-mousemove-on-btn',
                                isTrusted: e.isTrusted,
                                pointerType: e.pointerType || null,
                                buttons: e.buttons,
                                clientX: e.clientX,
                                clientY: e.clientY,
                                time: Date.now(),
                            });
                            if (eventLog.length > 50) eventLog.shift();
                        }
                    }, true);

                    window.__captchaEventLog = eventLog;
                    return {hooked: true};
                })()
            """,
        })
        log(f"Hook result: {result.output}")

        log("Now wait for manual drag or perform a test drag via eval...")
        await asyncio.sleep(2.0)

        log("Check event log")
        result = await browser({
            "action": "eval",
            "js": "JSON.stringify(window.__captchaEventLog || [], null, 2)",
        })
        log(f"Event log: {result.output}")

        log("\nKeeping browser open for 10s for manual inspection...")
        await asyncio.sleep(10.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
