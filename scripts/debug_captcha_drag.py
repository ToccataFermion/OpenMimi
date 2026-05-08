"""Debug script: trace exactly what happens during CAPTCHA drag attempts."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool
from openmimi.tools.computer import ComputerTool


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
    computer = ComputerTool()

    try:
        log("=== Step 1: Navigate and login ===")
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

        result = await browser({"action": "snapshot"})
        text = result.output or ""
        login_ref = None
        for line in text.splitlines():
            if "PasswordLogin_loginBtn" in line:
                import re
                m = re.search(r'ref=(e\d+)', line)
                if m:
                    login_ref = m.group(1)
                    log(f"  Found login ref: {login_ref}")
                    break

        if login_ref:
            await browser({"action": "click", "ref": login_ref})
        else:
            await browser({"action": "click", "ref": "e21"})
        await asyncio.sleep(4.0)

        log("\n=== Step 2: Query CAPTCHA element positions ===")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    // Check for iframes
                    const iframes = Array.from(document.querySelectorAll('iframe'));
                    const iframeInfo = iframes.map(f => ({
                        src: f.src,
                        id: f.id,
                        class: f.className,
                        rect: f.getBoundingClientRect(),
                        visible: f.offsetParent !== null,
                    }));

                    // Find CAPTCHA elements in main document
                    const selectors = ['.xftImageVerify', '.imageVerify', '.imageVerifyDrag', '.imageVerifyDragButton', '.bottomImage', '.dragImage'];
                    const mainDoc = {};
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const r = el.getBoundingClientRect();
                            mainDoc[sel] = {
                                found: true,
                                left: r.left, top: r.top, width: r.width, height: r.height,
                                right: r.right, bottom: r.bottom,
                                centerX: r.left + r.width/2, centerY: r.top + r.height/2,
                            };
                        } else {
                            mainDoc[sel] = {found: false};
                        }
                    }

                    // Window info
                    return {
                        iframes: iframeInfo,
                        mainDoc: mainDoc,
                        screenX: window.screenX, screenY: window.screenY,
                        outerWidth: window.outerWidth, outerHeight: window.outerHeight,
                        innerWidth: window.innerWidth, innerHeight: window.innerHeight,
                        dpr: window.devicePixelRatio,
                    };
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"  Window: screenX={data.get('screenX')}, screenY={data.get('screenY')}, outer={data.get('outerWidth')}x{data.get('outerHeight')}, inner={data.get('innerWidth')}x{data.get('innerHeight')}, dpr={data.get('dpr')}")
        log(f"  Iframes: {len(data.get('iframes', []))}")
        for f in data.get('iframes', []):
            log(f"    iframe: src={f.get('src','')[:60]}, rect={f.get('rect')}, visible={f.get('visible')}")
        for sel, info in data.get('mainDoc', {}).items():
            if info.get('found'):
                log(f"  {sel}: left={info['left']:.1f}, top={info['top']:.1f}, width={info['width']:.1f}, height={info['height']:.1f}, center=({info['centerX']:.1f}, {info['centerY']:.1f})")
            else:
                log(f"  {sel}: NOT FOUND")

        # Compute screen coordinates
        sx = data.get('screenX', 0)
        sy = data.get('screenY', 0)
        ow = data.get('outerWidth', 1280)
        oh = data.get('outerHeight', 800)
        iw = data.get('innerWidth', 1280)
        ih = data.get('innerHeight', 800)
        left_frame = (ow - iw) // 2
        top_frame = oh - ih - left_frame

        handle = data.get('mainDoc', {}).get('.imageVerifyDragButton', {})
        bg = data.get('mainDoc', {}).get('.bottomImage', {})

        if not handle.get('found'):
            log("  No CAPTCHA handle found!")
            return

        handle_sx = int(sx + left_frame + handle['centerX'])
        handle_sy = int(sy + top_frame + handle['centerY'])
        log(f"  Handle screen coords: ({handle_sx}, {handle_sy})")

        log("\n=== Step 3: Focus browser window ===")
        result = await browser({"action": "focus"})
        log(f"  Focus result: {result.output or 'null'}")
        await asyncio.sleep(0.5)

        log("\n=== Step 4: Cursor position before move ===")
        result = await computer({"action": "cursor_position"})
        log(f"  Cursor: {result.output}")

        log("\n=== Step 5: Move mouse to handle position ===")
        await computer({"action": "mouse_move", "x": handle_sx, "y": handle_sy})
        await asyncio.sleep(0.5)
        result = await computer({"action": "cursor_position"})
        log(f"  Cursor after move: {result.output}")

        log("\n=== Step 6: Take screenshot before drag ===")
        result = await computer({"action": "screenshot"})
        log(f"  Screenshot: {result.output}")

        log("\n=== Step 7: Try small drag (50px right) ===")
        await computer({"action": "mouse_drag", "x": handle_sx, "y": handle_sy, "end_x": handle_sx + 50, "end_y": handle_sy, "steps": 10})
        await asyncio.sleep(1.0)

        log("\n=== Step 8: Take screenshot after small drag ===")
        result = await computer({"action": "screenshot"})
        log(f"  Screenshot: {result.output}")

        log("\n=== Step 9: Check if slider moved via DOM ===")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const style = btn ? window.getComputedStyle(btn) : null;
                    const dragStyle = drag ? window.getComputedStyle(drag) : null;
                    return {
                        hasBtn: !!btn,
                        hasDrag: !!drag,
                        btnTransform: style ? style.transform : null,
                        dragTransform: dragStyle ? dragStyle.transform : null,
                        btnLeft: style ? style.left : null,
                    };
                })()
            """,
        })
        log(f"  DOM state after drag: {result.output}")

        log("\n=== Step 10: Try larger drag (200px right) ===")
        await computer({"action": "mouse_drag", "x": handle_sx, "y": handle_sy, "end_x": handle_sx + 200, "end_y": handle_sy, "steps": 30})
        await asyncio.sleep(1.0)

        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const style = btn ? window.getComputedStyle(btn) : null;
                    return {
                        btnTransform: style ? style.transform : null,
                        btnLeft: style ? style.left : null,
                    };
                })()
            """,
        })
        log(f"  DOM state after large drag: {result.output}")

        log("\nDone. Keeping browser open for 3s...")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
