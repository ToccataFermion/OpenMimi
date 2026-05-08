"""Try to bypass drag by setting handle position directly via JS."""
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

        # Try setting position directly and triggering events
        for pos in [100, 150, 200, 220, 240, 260]:
            log(f"\n--- Trying position {pos}px ---")
            result = await browser({
                "action": "eval",
                "js": f"""
                    (() => {{
                        const btn = document.querySelector('.imageVerifyDragButton');
                        const drag = document.querySelector('.dragImage');
                        if (btn) btn.style.left = '{pos}px';
                        if (drag) drag.style.left = '{pos}px';

                        // Try to trigger validation by dispatching events
                        if (btn) {{
                            btn.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, clientX: {pos}, clientY: 0 }}));
                            btn.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            btn.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                        document.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true }}));
                        window.dispatchEvent(new Event('resize'));

                        return {{set: true, btnLeft: btn ? btn.style.left : null}};
                    }})()
                """,
            })
            log(f"  Set result: {result.output}")
            await asyncio.sleep(1.5)

            result = await browser({
                "action": "eval",
                "js": """
                    (() => {
                        const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                        const btn = document.querySelector('.imageVerifyDragButton');
                        const drag = document.querySelector('.dragImage');
                        return {
                            hasVerify: !!verify,
                            btnLeft: btn ? window.getComputedStyle(btn).left : null,
                            dragLeft: drag ? window.getComputedStyle(drag).left : null,
                        };
                    })()
                """,
            })
            state = json.loads(result.output or "{}")
            log(f"  State: {json.dumps(state)}")
            if not state.get("hasVerify"):
                log(f"  SUCCESS at {pos}px!")
                break
            # Reset
            await browser({
                "action": "eval",
                "js": """
                    (() => {
                        const btn = document.querySelector('.imageVerifyDragButton');
                        const drag = document.querySelector('.dragImage');
                        if (btn) btn.style.left = '0px';
                        if (drag) drag.style.left = '0px';
                        return {reset: true};
                    })()
                """,
            })
            await asyncio.sleep(0.5)
        else:
            log("\nDirect JS manipulation did not work.")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
