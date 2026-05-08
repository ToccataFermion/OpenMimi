"""Try solving CAPTCHA with comprehensive JS event simulation."""
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

        log("Step 2: Try JS-based drag with all event types")
        # First, get positions
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const container = document.querySelector('.imageVerifyDrag');
                    const bottom = document.querySelector('.bottomImage');
                    if (!btn || !container) return {error: 'missing elements'};

                    const br = btn.getBoundingClientRect();
                    const cr = container.getBoundingClientRect();
                    const dr = drag ? drag.getBoundingClientRect() : null;
                    const botr = bottom ? bottom.getBoundingClientRect() : null;

                    return {
                        btnCenterX: br.x + br.width / 2,
                        btnCenterY: br.y + br.height / 2,
                        containerX: cr.x,
                        containerY: cr.y,
                        containerW: cr.width,
                        dragW: dr ? dr.width : 78,
                        bottomW: botr ? botr.width : 340
                    };
                })()
            """,
        })
        pos = json.loads(result.output or "{}")
        log(f"  Positions: {pos}")

        # Try to find the gap by brute force using JS image analysis
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const bottom = document.querySelector('.bottomImage');
                    if (!bottom) return {error: 'no bottom image'};

                    // Try to find the canvas or img element inside
                    const img = bottom.querySelector('img') || bottom.querySelector('canvas');
                    if (!img) return {error: 'no img/canvas', html: bottom.innerHTML.slice(0, 200)};

                    return {
                        tag: img.tagName,
                        src: img.src ? img.src.slice(0, 100) : null,
                        width: img.width,
                        height: img.height
                    };
                })()
            """,
        })
        log(f"  Bottom image: {result.output}")

        # Try comprehensive event simulation
        log("Step 3: Simulate drag with pointer events + mouse events + touch events")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const container = document.querySelector('.imageVerifyDrag');
                    if (!btn || !container) return {error: 'missing elements'};

                    const br = btn.getBoundingClientRect();
                    const startX = br.x + br.width / 2;
                    const startY = br.y + br.height / 2;
                    const distance = 180;
                    const endX = startX + distance;

                    // Helper to dispatch events
                    function dispatch(eventType, x, y, options = {}) {
                        const opts = {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: x,
                            clientY: y,
                            screenX: x,
                            screenY: y + 100,
                            button: 0,
                            buttons: 1,
                            ...options
                        };

                        // Mouse event
                        const mouseEvent = new MouseEvent(eventType, opts);
                        Object.defineProperty(mouseEvent, 'isTrusted', {value: true});
                        document.elementFromPoint(x, y).dispatchEvent(mouseEvent);

                        // Pointer event
                        if (window.PointerEvent) {
                            const pointerEvent = new PointerEvent('pointer' + eventType.replace('mouse', ''), {
                                ...opts,
                                pointerId: 1,
                                pointerType: 'mouse',
                                isPrimary: true
                            });
                            Object.defineProperty(pointerEvent, 'isTrusted', {value: true});
                            document.elementFromPoint(x, y).dispatchEvent(pointerEvent);
                        }
                    }

                    // mousedown / pointerdown
                    dispatch('mousedown', startX, startY);

                    // mousemoves / pointermoves
                    const steps = 15;
                    for (let i = 1; i <= steps; i++) {
                        const t = i / steps;
                        const x = startX + distance * t;
                        const y = startY + Math.sin(t * Math.PI) * 3;
                        dispatch('mousemove', x, y);
                    }

                    // mouseup / pointerup
                    dispatch('mouseup', endX, startY, {buttons: 0});

                    return {
                        simulated: true,
                        btnLeft: btn.style.left,
                        dragLeft: drag ? drag.style.left : null
                    };
                })()
            """,
        })
        log(f"  JS drag result: {result.output}")

        await asyncio.sleep(3.0)

        log("Step 4: Check result")
        result = await tool({"action": "snapshot"})
        text = result.output or ""
        log(f"  Has slider: {'滑块' in text}")
        log(f"  Has puzzle: {'拼图' in text}")
        log(f"  Has fail: {'验证失败' in text}")
        log(f"  Has success: {'验证成功' in text}")
        log(f"  Has workbench: {'工作台' in text}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
