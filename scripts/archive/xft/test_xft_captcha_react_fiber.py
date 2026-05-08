"""Try to access React fiber and trigger CAPTCHA validation directly."""
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

        log("Step 2: Access React fiber and find callback")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const container = document.querySelector('.imageVerifyDrag');
                    const verify = document.querySelector('.xftImageVerify');

                    function getFiber(el) {
                        const keys = Object.keys(el);
                        const reactKey = keys.find(k => k.startsWith('__reactInternalInstance') || k.startsWith('__reactFiber'));
                        return reactKey ? el[reactKey] : null;
                    }

                    function findCallbacks(fiber) {
                        const callbacks = [];
                        let node = fiber;
                        for (let i = 0; i < 30 && node; i++) {
                            if (node.memoizedProps) {
                                const props = node.memoizedProps;
                                for (const key of Object.keys(props)) {
                                    const val = props[key];
                                    if (typeof val === 'function') {
                                        const src = val.toString();
                                        if (src.includes('verify') || src.includes('success') || src.includes('captcha') || src.includes('slide') || src.includes('check') || src.includes('validate')) {
                                            callbacks.push({prop: key, src: src.slice(0, 200)});
                                        }
                                    }
                                }
                            }
                            if (node.memoizedState && typeof node.memoizedState === 'object') {
                                const state = node.memoizedState;
                                for (const key of Object.keys(state)) {
                                    const val = state[key];
                                    if (typeof val === 'function') {
                                        const src = val.toString();
                                        if (src.includes('verify') || src.includes('success') || src.includes('captcha') || src.includes('slide') || src.includes('check') || src.includes('validate')) {
                                            callbacks.push({state: key, src: src.slice(0, 200)});
                                        }
                                    }
                                }
                            }
                            node = node.return;
                        }
                        return callbacks;
                    }

                    const btnFiber = getFiber(btn);
                    const dragFiber = drag ? getFiber(drag) : null;
                    const containerFiber = getFiber(container);
                    const verifyFiber = verify ? getFiber(verify) : null;

                    return {
                        hasBtnFiber: !!btnFiber,
                        hasDragFiber: !!dragFiber,
                        hasContainerFiber: !!containerFiber,
                        hasVerifyFiber: !!verifyFiber,
                        btnCallbacks: btnFiber ? findCallbacks(btnFiber) : [],
                        dragCallbacks: dragFiber ? findCallbacks(dragFiber) : [],
                        containerCallbacks: containerFiber ? findCallbacks(containerFiber) : [],
                        verifyCallbacks: verifyFiber ? findCallbacks(verifyFiber) : []
                    };
                })()
            """,
        })
        log(f"  Fiber callbacks: {result.output[:3000] if result.output else 'empty'}")

        log("Step 3: Look for drag handler functions")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const container = document.querySelector('.imageVerifyDrag');

                    function getFiber(el) {
                        const keys = Object.keys(el);
                        const reactKey = keys.find(k => k.startsWith('__reactInternalInstance') || k.startsWith('__reactFiber'));
                        return reactKey ? el[reactKey] : null;
                    }

                    function findAllFunctions(fiber) {
                        const funcs = [];
                        let node = fiber;
                        for (let i = 0; i < 30 && node; i++) {
                            if (node.memoizedProps) {
                                const props = node.memoizedProps;
                                for (const key of Object.keys(props)) {
                                    const val = props[key];
                                    if (typeof val === 'function') {
                                        funcs.push({type: 'prop', key, src: val.toString().slice(0, 150)});
                                    }
                                }
                            }
                            node = node.return;
                        }
                        return funcs;
                    }

                    const containerFiber = getFiber(container);
                    const allFuncs = containerFiber ? findAllFunctions(containerFiber) : [];
                    return allFuncs.slice(0, 20);
                })()
            """,
        })
        log(f"  All functions: {result.output[:3000] if result.output else 'empty'}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
