"""Debug script v2: try multiple drag approaches on CAPTCHA."""
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


async def try_drag(computer: ComputerTool, browser: AgentBrowserTool,
                   label: str, sx: int, sy: int, ex: int, ey: int) -> None:
    log(f"\n--- {label}: drag ({sx},{sy}) -> ({ex},{ey}) ---")
    await computer({"action": "mouse_move", "x": sx, "y": sy})
    await asyncio.sleep(0.3)
    await computer({"action": "mouse_drag", "x": sx, "y": sy, "end_x": ex, "end_y": ey, "steps": 30})
    await asyncio.sleep(0.5)
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.imageVerifyDragButton');
                const drag = document.querySelector('.dragImage');
                const style = btn ? window.getComputedStyle(btn) : null;
                const dragStyle = drag ? window.getComputedStyle(drag) : null;
                return {
                    btnLeft: style ? style.left : null,
                    btnTransform: style ? style.transform : null,
                    dragTransform: dragStyle ? dragStyle.transform : null,
                };
            })()
        """,
    })
    log(f"  Result: {result.output}")


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
        # Find and click the actual login button (not the agreement checkbox)
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                    if (btn) {
                        const r = btn.getBoundingClientRect();
                        btn.click();
                        return {clicked: true, class: btn.className, rect: {left: r.left, top: r.top, width: r.width, height: r.height}};
                    }
                    // fallback: try any element containing login text
                    const all = Array.from(document.querySelectorAll('div, button, a'));
                    const fb = all.find(el => el.textContent && el.textContent.includes('登录'));
                    if (fb) { fb.click(); return {clicked: true, fallback: true, text: fb.textContent.trim()}; }
                    return {clicked: false};
                })()
            """,
        })
        log(f"  Login click result: {result.output}")
        await asyncio.sleep(4.0)

        log("\n=== Query positions ===")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const els = {};
                    for (const sel of ['.imageVerifyDragButton', '.dragImage', '.imageVerifyDrag', '.bottomImage']) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const r = el.getBoundingClientRect();
                            els[sel] = {left: r.left, top: r.top, width: r.width, height: r.height, cx: r.left+r.width/2, cy: r.top+r.height/2};
                        }
                    }
                    const sx = window.screenX + (window.outerWidth - window.innerWidth) / 2;
                    const sy = window.screenY + window.outerHeight - window.innerHeight - (window.outerWidth - window.innerWidth) / 2;
                    return {els, sx, sy, dpr: window.devicePixelRatio};
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        sx = data.get("sx", 0)
        sy = data.get("sy", 0)
        log(f"  Frame offset: ({sx}, {sy})")
        positions = {}
        for sel, info in data.get("els", {}).items():
            screen_x = int(sx + info["cx"])
            screen_y = int(sy + info["cy"])
            positions[sel] = (screen_x, screen_y)
            log(f"  {sel}: viewport=({info['cx']:.1f}, {info['cy']:.1f}), screen=({screen_x}, {screen_y})")

        await browser({"action": "focus"})
        await asyncio.sleep(0.5)

        # Approach 1: drag handle button
        if ".imageVerifyDragButton" in positions:
            x, y = positions[".imageVerifyDragButton"]
            await try_drag(computer, browser, "Handle button", x, y, x + 150, y)

        # Approach 2: drag puzzle piece
        if ".dragImage" in positions:
            x, y = positions[".dragImage"]
            await try_drag(computer, browser, "Puzzle piece", x, y, x + 150, y)

        # Approach 3: drag track
        if ".imageVerifyDrag" in positions:
            x, y = positions[".imageVerifyDrag"]
            await try_drag(computer, browser, "Drag track", x, y, x + 150, y)

        # Approach 4: hover then drag handle (simulate natural entry)
        if ".imageVerifyDragButton" in positions:
            x, y = positions[".imageVerifyDragButton"]
            log(f"\n--- Natural entry: hover left of handle then drag ---")
            await computer({"action": "mouse_move", "x": x - 50, "y": y})
            await asyncio.sleep(0.2)
            # Move slowly into handle
            for i in range(1, 6):
                await computer({"action": "mouse_move", "x": x - 50 + i * 10, "y": y})
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.2)
            await computer({"action": "mouse_drag", "x": x, "y": y, "end_x": x + 150, "end_y": y, "steps": 30})
            await asyncio.sleep(0.5)
            result = await browser({
                "action": "eval",
                "js": "(() => { const s = document.querySelector('.imageVerifyDragButton')?.getBoundingClientRect(); return {left: s?.left}; })()",
            })
            log(f"  Result: {result.output}")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
