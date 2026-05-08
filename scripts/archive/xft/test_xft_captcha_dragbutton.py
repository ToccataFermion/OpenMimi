"""Target the actual .imageVerifyDragButton for slider CAPTCHA solving."""
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

        log("Step 4: Inspect drag button")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const progress = document.querySelector('.imageVerifyDragProgressbar');
                    const text = document.querySelector('.imageVerifyDragText');
                    if (!btn) return {error: 'button not found'};
                    const r = btn.getBoundingClientRect();
                    const dr = drag ? drag.getBoundingClientRect() : null;
                    return {
                        btnRect: {x: r.x, y: r.y, w: r.width, h: r.height},
                        dragRect: dr ? {x: dr.x, y: dr.y, w: dr.width, h: dr.height} : null,
                        btnClass: btn.className,
                        btnHTML: btn.outerHTML?.slice(0, 200),
                        text: text?.textContent?.trim()?.slice(0, 50),
                        progressWidth: progress?.style?.width
                    };
                })()
            """,
        })
        log(f"  Drag button: {result.output}")

        drag_info = json.loads(result.output or "{}")
        btn_rect = drag_info.get("btnRect")
        if not btn_rect:
            log("  Drag button not found, aborting")
            return

        log("Step 5: Get bottom image and try to estimate gap")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const bottom = document.querySelector('.bottomImage');
                    if (!bottom) return {error: 'no bottom image'};
                    const r = bottom.getBoundingClientRect();
                    return {x: r.x, y: r.y, w: r.width, h: r.height};
                })()
            """,
        })
        bottom_rect = json.loads(result.output or "{}")
        log(f"  Bottom image: {bottom_rect}")

        # Try dragging the button to a few positions
        # The background is 340px wide, piece is 78px wide
        # Gap is somewhere between 78 and 262 px from left edge of background
        # Let's try dragging by ~150px first as a reasonable guess
        start_x = int(btn_rect["x"] + btn_rect["w"] / 2)
        start_y = int(btn_rect["y"] + btn_rect["h"] / 2)
        distance = 180  # Reasonable guess

        log(f"Step 6: Drag button from ({start_x}, {start_y}) by {distance}px")
        await tool._exec("mouse", "move", str(start_x), str(start_y), "--json")
        await asyncio.sleep(0.2)
        await tool._exec("mouse", "down", "--json")
        await asyncio.sleep(0.2)

        steps = 30
        for i in range(1, steps + 1):
            t = i / steps
            ease = 1 - (1 - t) ** 3
            cx = start_x + int(distance * ease)
            cy = start_y + (i % 3 - 1)
            await tool._exec("mouse", "move", str(cx), str(cy), "--json")
            await asyncio.sleep(0.04 + (i % 5) * 0.005)

        await tool._exec("mouse", "up", "--json")
        await asyncio.sleep(0.2)
        log("  Drag complete")

        await asyncio.sleep(3.0)

        log("Step 7: Check result")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Snapshot len: {len(text)}")
        log(f"  Contains '滑块': {'滑块' in text}")
        log(f"  Contains '拼图': {'拼图' in text}")
        log(f"  Contains '密码登录': {'密码登录' in text}")
        log(f"  Contains '验证失败': {'验证失败' in text}")
        log(f"  Contains '验证成功': {'验证成功' in text}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
