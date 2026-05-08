"""Try to find and call the actual verify-success callback."""
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

        log("Step 2: Inspect ALL props on xftImageVerify fiber")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const verify = document.querySelector('.xftImageVerify');

                    function getFiber(el) {
                        const keys = Object.keys(el);
                        const reactKey = keys.find(k => k.startsWith('__reactInternalInstance') || k.startsWith('__reactFiber'));
                        return reactKey ? el[reactKey] : null;
                    }

                    function inspectFiber(fiber) {
                        let node = fiber;
                        const results = [];
                        for (let i = 0; i < 50 && node; i++) {
                            if (node.memoizedProps) {
                                const props = node.memoizedProps;
                                const propInfo = {};
                                for (const key of Object.keys(props)) {
                                    const val = props[key];
                                    if (typeof val === 'function') {
                                        propInfo[key] = val.toString().slice(0, 200);
                                    } else if (typeof val === 'string' || typeof val === 'number' || typeof val === 'boolean') {
                                        propInfo[key] = val;
                                    } else if (val === null || val === undefined) {
                                        propInfo[key] = String(val);
                                    }
                                }
                                if (Object.keys(propInfo).length > 0) {
                                    results.push({depth: i, tag: node.type?.name || node.type, props: propInfo});
                                }
                            }
                            node = node.return;
                        }
                        return results;
                    }

                    const fiber = getFiber(verify);
                    return inspectFiber(fiber);
                })()
            """,
        })
        log(f"  All props: {result.output[:4000] if result.output else 'empty'}")

        log("Step 3: Try calling onCloseVerify with a mock token")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const verify = document.querySelector('.xftImageVerify');

                    function getFiber(el) {
                        const keys = Object.keys(el);
                        const reactKey = keys.find(k => k.startsWith('__reactInternalInstance') || k.startsWith('__reactFiber'));
                        return reactKey ? el[reactKey] : null;
                    }

                    function findProp(fiber, propName) {
                        let node = fiber;
                        for (let i = 0; i < 50 && node; i++) {
                            if (node.memoizedProps && node.memoizedProps[propName] && typeof node.memoizedProps[propName] === 'function') {
                                return {fn: node.memoizedProps[propName], depth: i};
                            }
                            node = node.return;
                        }
                        return null;
                    }

                    const fiber = getFiber(verify);
                    const closeCb = findProp(fiber, 'onCloseVerify');
                    const results = {};
                    if (closeCb) {
                        try {
                            // Try calling with a token
                            closeCb.fn({token: 'mock_token_123', success: true});
                            results.withToken = true;
                        } catch (e) {
                            results.withTokenError = e.message;
                        }
                        try {
                            closeCb.fn(true);
                            results.withTrue = true;
                        } catch (e) {
                            results.withTrueError = e.message;
                        }
                        try {
                            closeCb.fn();
                            results.withNothing = true;
                        } catch (e) {
                            results.withNothingError = e.message;
                        }
                    }
                    return results;
                })()
            """,
        })
        log(f"  CloseVerify attempts: {result.output}")

        await asyncio.sleep(2.0)

        log("Step 4: Check if login proceeded")
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
