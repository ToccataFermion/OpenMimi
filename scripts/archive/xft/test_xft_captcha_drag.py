"""Try to solve xft slider CAPTCHA using agent-browser drag/mouse commands."""
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

        log("Step 4: Get all CAPTCHA-related element positions")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const els = [];
                    all.forEach(el => {
                        const cls = el.className;
                        const id = el.id;
                        if (typeof cls === 'string' && (cls.includes('drag') || cls.includes('slider') || cls.includes('handle') || cls.includes('captcha') || cls.includes('verify') || cls.includes('bottom') || cls.includes('refresh'))) {
                            const r = el.getBoundingClientRect();
                            els.push({tag: el.tagName, class: cls.slice(0, 60), id, rect: {x: r.x, y: r.y, w: r.width, h: r.height}});
                        }
                    });
                    return els;
                })()
            """,
        })
        log(f"  CAPTCHA elements: {result.output}")

        log("Step 5: Inspect slider handle and track")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    // Find the actual slider control (not the image)
                    const slider = all.find(el => {
                        const cls = typeof el.className === 'string' ? el.className : '';
                        const txt = (el.textContent || '').trim();
                        return cls.includes('dragButton') || cls.includes('slider') || cls.includes('handle') || txt.includes('>>') || txt.includes('→');
                    });
                    if (slider) {
                        const r = slider.getBoundingClientRect();
                        return {found: true, tag: slider.tagName, class: slider.className.slice(0, 60), rect: {x: r.x, y: r.y, w: r.width, h: r.height}};
                    }
                    // Also look for input range or draggable div
                    const range = document.querySelector('input[type="range"]');
                    if (range) {
                        const r = range.getBoundingClientRect();
                        return {found: true, tag: 'INPUT', type: 'range', rect: {x: r.x, y: r.y, w: r.width, h: r.height}};
                    }
                    return {found: false};
                })()
            """,
        })
        log(f"  Slider handle: {result.output}")

        log("Step 6: Try agent-browser mouse drag")
        # Get drag image position
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const drag = document.querySelector('.dragImage');
                    if (!drag) return null;
                    const r = drag.getBoundingClientRect();
                    return {x: r.x + r.width/2, y: r.y + r.height/2, w: r.width, h: r.height};
                })()
            """,
        })
        drag_pos = json.loads(result.output or "null") if result.output else None
        log(f"  Drag image center: {drag_pos}")

        # Try to use agent-browser's drag command if we have selectors
        # Or use mouse commands
        if drag_pos:
            start_x = int(drag_pos["x"])
            start_y = int(drag_pos["y"])
            end_x = start_x + 150
            end_y = start_y

            log(f"  Attempting mouse drag from ({start_x}, {start_y}) to ({end_x}, {end_y})")
            try:
                # Use raw exec to send mouse commands
                await tool._exec("mouse", "move", str(start_x), str(start_y), "--json")
                await asyncio.sleep(0.2)
                await tool._exec("mouse", "down", "--json")
                await asyncio.sleep(0.2)

                # Move in steps
                steps = 10
                for i in range(1, steps + 1):
                    t = i / steps
                    ease = 1 - (1 - t) ** 3
                    cx = start_x + int((end_x - start_x) * ease)
                    cy = start_y + int(2 * (i % 2 == 0 and 1 or -1))
                    await tool._exec("mouse", "move", str(cx), str(cy), "--json")
                    await asyncio.sleep(0.05)

                await tool._exec("mouse", "up", "--json")
                await asyncio.sleep(0.2)
                log("  Mouse drag complete")
            except Exception as e:
                log(f"  Mouse drag error: {e}")

        await asyncio.sleep(3.0)

        log("Step 7: Check result")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Snapshot length: {len(text)}")
        log(f"  Contains '滑块': {'滑块' in text}")
        log(f"  Contains '拼图': {'拼图' in text}")
        log(f"  Contains '工作台': {'工作台' in text}")
        log(f"  Contains '首页': {'首页' in text}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
