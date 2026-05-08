"""Check what happens during/after drag to understand CAPTCHA mechanics."""
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

        log("Step 4: Inspect drag structure and listeners")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const bottom = document.querySelector('.bottomImage');
                    const container = document.querySelector('.imageVerifyDrag');
                    const track = document.querySelector('.imageVerifyDragProgressbar');

                    if (!btn) return {error: 'button not found'};

                    // Check parent structure
                    let parentChain = [];
                    let el = btn;
                    for (let i = 0; i < 5 && el; i++) {
                        parentChain.push({
                            tag: el.tagName,
                            class: typeof el.className === 'string' ? el.className.slice(0, 60) : 'n/a',
                            id: el.id
                        });
                        el = el.parentElement;
                    }

                    // Check computed styles
                    const btnStyle = window.getComputedStyle(btn);
                    const dragStyle = drag ? window.getComputedStyle(drag) : null;

                    return {
                        btnRect: {x: btn.getBoundingClientRect().x, y: btn.getBoundingClientRect().y, w: btn.getBoundingClientRect().width, h: btn.getBoundingClientRect().height},
                        dragRect: drag ? {x: drag.getBoundingClientRect().x, y: drag.getBoundingClientRect().y, w: drag.getBoundingClientRect().width, h: drag.getBoundingClientRect().height} : null,
                        btnLeft: btn.style.left,
                        dragLeft: drag ? drag.style.left : null,
                        btnPosition: btnStyle.position,
                        dragPosition: dragStyle ? dragStyle.position : null,
                        btnCursor: btnStyle.cursor,
                        parentChain,
                        containerClass: container ? container.className : null,
                        trackWidth: track ? track.style.width : null
                    };
                })()
            """,
        })
        log(f"  Structure: {result.output}")

        log("Step 5: Perform mouse drag and check intermediate state")
        btn_rect = json.loads(result.output or "{}").get("btnRect", {})
        start_x = int(btn_rect.get("x", 459) + btn_rect.get("w", 60) / 2)
        start_y = int(btn_rect.get("y", 442) + btn_rect.get("h", 40) / 2)

        # Move to button
        await tool._exec("mouse", "move", str(start_x), str(start_y), "--json")
        await asyncio.sleep(0.2)

        # Mouse down
        await tool._exec("mouse", "down", "--json")
        await asyncio.sleep(0.2)

        # Move a bit and check state
        mid_x = start_x + 50
        await tool._exec("mouse", "move", str(mid_x), str(start_y), "--json")
        await asyncio.sleep(0.5)

        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const track = document.querySelector('.imageVerifyDragProgressbar');
                    return {
                        btnLeft: btn ? btn.style.left : null,
                        dragLeft: drag ? drag.style.left : null,
                        btnRect: btn ? {x: btn.getBoundingClientRect().x, y: btn.getBoundingClientRect().y} : null,
                        dragRect: drag ? {x: drag.getBoundingClientRect().x, y: drag.getBoundingClientRect().y} : null,
                        trackWidth: track ? track.style.width : null
                    };
                })()
            """,
        })
        log(f"  After 50px drag: {result.output}")

        # Continue drag
        end_x = start_x + 180
        for cx in range(mid_x + 10, end_x + 1, 10):
            await tool._exec("mouse", "move", str(cx), str(start_y), "--json")
            await asyncio.sleep(0.05)

        await tool._exec("mouse", "up", "--json")
        await asyncio.sleep(0.5)

        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const track = document.querySelector('.imageVerifyDragProgressbar');
                    const text = document.querySelector('.imageVerifyDragText');
                    return {
                        btnLeft: btn ? btn.style.left : null,
                        dragLeft: drag ? drag.style.left : null,
                        btnRect: btn ? {x: btn.getBoundingClientRect().x, y: btn.getBoundingClientRect().y} : null,
                        dragRect: drag ? {x: drag.getBoundingClientRect().x, y: drag.getBoundingClientRect().y} : null,
                        trackWidth: track ? track.style.width : null,
                        text: text ? text.textContent.trim().slice(0, 50) : null
                    };
                })()
            """,
        })
        log(f"  After full drag: {result.output}")

        log("Step 6: Try JS-based drag simulation")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const container = document.querySelector('.imageVerifyDrag');
                    if (!btn || !container) return {error: 'missing elements'};

                    const cr = container.getBoundingClientRect();
                    const br = btn.getBoundingClientRect();

                    // Simulate mousedown on button
                    const downEvent = new MouseEvent('mousedown', {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                        clientX: br.x + br.width / 2,
                        clientY: br.y + br.height / 2,
                        button: 0
                    });
                    btn.dispatchEvent(downEvent);

                    // Simulate mousemove to target
                    const targetX = br.x + br.width / 2 + 150;
                    const moveEvent = new MouseEvent('mousemove', {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                        clientX: targetX,
                        clientY: br.y + br.height / 2,
                        button: 0
                    });
                    document.dispatchEvent(moveEvent);

                    // Simulate mouseup
                    const upEvent = new MouseEvent('mouseup', {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                        clientX: targetX,
                        clientY: br.y + br.height / 2,
                        button: 0
                    });
                    document.dispatchEvent(upEvent);

                    return {
                        simulated: true,
                        btnLeftAfter: btn.style.left,
                        dragLeftAfter: drag ? drag.style.left : null
                    };
                })()
            """,
        })
        log(f"  JS drag sim: {result.output}")

        await asyncio.sleep(2.0)

        log("Step 7: Check final state")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Snapshot len: {len(text)}")
        log(f"  Contains slider: {'滑块' in text}")
        log(f"  Contains puzzle: {'拼图' in text}")
        log(f"  Contains fail: {'验证失败' in text}")
        log(f"  Contains success: {'验证成功' in text}")
        log(f"  Contains workbench: {'工作台' in text}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
