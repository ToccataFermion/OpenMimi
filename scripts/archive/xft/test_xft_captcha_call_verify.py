"""Try to call CAPTCHA verification callback directly via React fiber."""
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

        log("Step 2: Find and call verification callback")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const container = document.querySelector('.imageVerifyDrag');
                    const verify = document.querySelector('.xftImageVerify');
                    const imageVerify = document.querySelector('.imageVerify');

                    function getFiber(el) {
                        const keys = Object.keys(el);
                        const reactKey = keys.find(k => k.startsWith('__reactInternalInstance') || k.startsWith('__reactFiber'));
                        return reactKey ? el[reactKey] : null;
                    }

                    // Walk up the fiber tree looking for onCloseVerify or onVerifySuccess
                    function findVerifyCallback(fiber) {
                        let node = fiber;
                        for (let i = 0; i < 50 && node; i++) {
                            if (node.memoizedProps) {
                                const props = node.memoizedProps;
                                if (props.onCloseVerify && typeof props.onCloseVerify === 'function') {
                                    return {name: 'onCloseVerify', fn: props.onCloseVerify, depth: i};
                                }
                                if (props.onVerify && typeof props.onVerify === 'function') {
                                    return {name: 'onVerify', fn: props.onVerify, depth: i};
                                }
                                if (props.onSuccess && typeof props.onSuccess === 'function') {
                                    return {name: 'onSuccess', fn: props.onSuccess, depth: i};
                                }
                                if (props.onChange && typeof props.onChange === 'function') {
                                    return {name: 'onChange', fn: props.onChange, depth: i};
                                }
                            }
                            // Also check state for dispatch or setState
                            if (node.memoizedState && typeof node.memoizedState === 'object') {
                                const state = node.memoizedState;
                                for (const key of Object.keys(state)) {
                                    const val = state[key];
                                    if (typeof val === 'function' && (key.includes('dispatch') || key.includes('set') || key.includes('verify'))) {
                                        return {name: 'state.' + key, fn: val, depth: i};
                                    }
                                }
                            }
                            node = node.return;
                        }
                        return null;
                    }

                    // Try multiple starting points
                    const candidates = [container, verify, imageVerify].filter(Boolean);
                    const results = [];
                    for (const el of candidates) {
                        const fiber = getFiber(el);
                        if (fiber) {
                            const cb = findVerifyCallback(fiber);
                            results.push({
                                selector: el.className,
                                hasFiber: true,
                                callback: cb ? {name: cb.name, depth: cb.depth, src: cb.fn.toString().slice(0, 200)} : null
                            });
                            if (cb) {
                                try {
                                    cb.fn();
                                    results[results.length - 1].called = true;
                                } catch (e) {
                                    results[results.length - 1].called = false;
                                    results[results.length - 1].error = e.message;
                                }
                            }
                        }
                    }
                    return results;
                })()
            """,
        })
        log(f"  Verify attempt: {result.output[:2000] if result.output else 'empty'}")

        await asyncio.sleep(2.0)

        log("Step 3: Check result")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Has slider: {'滑块' in text}")
        log(f"  Has puzzle: {'拼图' in text}")
        log(f"  Has fail: {'验证失败' in text}")
        log(f"  Has success: {'验证成功' in text}")
        log(f"  Has workbench: {'工作台' in text}")
        log(f"  Has password login: {'密码登录' in text}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
