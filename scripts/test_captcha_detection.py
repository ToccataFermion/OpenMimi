"""Test element-based CAPTCHA detection on xft login page."""
from __future__ import annotations

import asyncio
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
        log("Step 1: Navigate to xft")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)

        log("Step 2: Click login tab")
        await browser({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Step 3: Take snapshot BEFORE login click (should NOT detect CAPTCHA)")
        result = await browser({"action": "snapshot"})
        has_captcha = result.details.get("captcha_detected", False) if result.details else False
        log(f"  CAPTCHA detected: {has_captcha}")
        if has_captcha:
            log("  FAIL: False positive - CAPTCHA detected before login click!")
            return
        log("  PASS: No false positive")

        log("Step 4: Fill credentials")
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const inputs = Array.from(document.querySelectorAll('input.ant-input'));
                    const phone = inputs.find(el => el.type === 'text');
                    const pass = inputs.find(el => el.type === 'password');
                    function setReactValue(element, value) {
                        const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        valueSetter.call(element, value);
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    if (phone) setReactValue(phone, '18584828398');
                    if (pass) setReactValue(pass, 'Liszt123');
                    return {ok: true};
                })()
            """,
        })
        await asyncio.sleep(0.5)

        log("Step 5: Find login button ref and click it")
        result = await browser({"action": "snapshot"})
        text = result.output or ""
        # Find login button ref - look for PasswordLogin_loginBtn in snapshot text
        login_ref = None
        for line in text.splitlines():
            if "PasswordLogin_loginBtn" in line or "登录" in line:
                # Extract ref from line like "... [ref=e15] ..."
                import re
                m = re.search(r'ref=(e\d+)', line)
                if m:
                    login_ref = m.group(1)
                    log(f"  Found login button: {login_ref} in line: {line.strip()[:120]}")
                    break

        if login_ref:
            await browser({"action": "click", "ref": login_ref})
        else:
            log("  No login button ref found, trying eval click")
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
        await asyncio.sleep(5.0)

        log("Step 6: Take snapshot AFTER login click (should detect CAPTCHA)")
        result = await browser({"action": "snapshot"})
        text = result.output or ""
        has_captcha = result.details.get("captcha_detected", False) if result.details else False
        log(f"  CAPTCHA detected: {has_captcha}")
        log(f"  Snapshot length: {len(text)}")

        # Debug: manually check for CAPTCHA elements
        debug = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const selectors = ['.xftImageVerify', '.imageVerifyDragButton', '.bottomImage', '.dragImage', '.imageVerify'];
                    const results = {};
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            results[sel] = {
                                found: true,
                                display: style.display,
                                visibility: style.visibility,
                                width: rect.width,
                                height: rect.height,
                            };
                        } else {
                            results[sel] = {found: false};
                        }
                    }
                    return results;
                })()
            """,
        })
        log(f"  Debug element check: {debug.output or 'null'}")

        if not has_captcha:
            log("  FAIL: CAPTCHA not detected after login click!")
            log(f"  Output preview: {text[:500]}")
            return
        log("  PASS: CAPTCHA correctly detected")

        log("\nAll tests passed!")

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
